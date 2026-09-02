from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_DISABLED"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test-production-foundation.db"
os.environ["DATA_DIR"] = "./data/test-storage"
os.environ["EXTRACTOR_STRATEGY"] = "mock"

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf

from app.extractor import (
    ExtractionResult,
    OpenAIVisionStrategy,
    add_filename_extension,
    prepare_openai_media,
    validate_prompt_contract,
)
from app.main import app
from evals.runner import compute_metrics


DB_PATH = Path("data/test-production-foundation.db")
ALL_ROLES = "document.submit,document.read,document.review,document.admin"


def auth(user: str = "operador.demo", roles: str = ALL_ROLES) -> dict[str, str]:
    return {"X-Dev-User": user, "X-Dev-Roles": roles}


def make_png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), color).save(buffer, format="PNG")
    return buffer.getvalue()


def mark_for_review(*document_ids: str) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.executemany(
            """
            UPDATE documents
            SET status = 'NEEDS_REVIEW',
                document_type = 'RG',
                filename_suggested = 'rg_teste.png',
                extracted_data = '{"nome": "NOME TESTE"}',
                confidence_score = 0.70
            WHERE id = ?
            """,
            [(document_id,) for document_id in document_ids],
        )
        connection.commit()


def test_production_foundation_vertical_slice() -> None:
    for suffix in ("", "-shm", "-wal"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        config = client.get("/api/config").json()
        assert config["auth_disabled"] is True

        forbidden = client.post(
            "/api/v1/documents",
            headers=auth(roles="document.read"),
            files={"file": ("doc.png", make_png((1, 2, 3)), "image/png")},
            data={"source_channel": "email"},
        )
        assert forbidden.status_code == 403

        invalid = client.post(
            "/api/v1/documents",
            headers=auth(),
            files={"file": ("malware.jpg", b"not-an-image", "image/jpeg")},
            data={"source_channel": "whatsapp"},
        )
        assert invalid.status_code == 415

        first_bytes = make_png((18, 184, 209))
        first = client.post(
            "/api/v1/documents",
            headers=auth("atendimento"),
            files={"file": ("cpf_12345678901.png", first_bytes, "image/png")},
            data={"source_channel": "whatsapp"},
        )
        assert first.status_code == 201
        assert first.json()["duplicate"] is False
        first_id = first.json()["document"]["id"]

        duplicate = client.post(
            "/api/v1/documents",
            headers=auth("atendimento"),
            files={"file": ("outro_nome.png", first_bytes, "image/png")},
            data={"source_channel": "email"},
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["document"]["id"] == first_id

        second = client.post(
            "/api/v1/documents",
            headers=auth("atendimento"),
            files={"file": ("rg_99887766.png", make_png((244, 166, 42)), "image/png")},
            data={"source_channel": "balcao"},
        )
        second_id = second.json()["document"]["id"]
        assert len(client.app.state.queue.enqueued) == 2
        assert awaitable_storage_count(client) == 2

        mark_for_review(first_id, second_id)

        def claim(user: str):
            response = client.post(
                "/api/v1/review/claim",
                headers=auth(user, "document.read,document.review"),
                json={},
            )
            assert response.status_code == 200
            return response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, ["operador.a", "operador.b"]))

        assert {item["id"] for item in claims} == {first_id, second_id}
        assert claims[0]["id"] != claims[1]["id"]

        approved = claims[0]
        submit = client.post(
            f"/api/v1/review/{approved['id']}/submit",
            headers=auth(approved["claimed_by"], "document.read,document.review"),
            json={
                "decision": "APPROVE",
                "document_type": approved["document_type"],
                "filename_suggested": approved["filename_suggested"],
                "extracted_data": approved["extracted_data"],
                "notes": "Conferido no teste.",
            },
        )
        assert submit.status_code == 200
        assert submit.json()["status"] == "COMPLETED"

        hold = client.patch(
            f"/api/v1/documents/{approved['id']}/retention",
            headers=auth("admin"),
            json={"legal_hold": True, "reason": "Preservação para processo interno."},
        )
        assert hold.status_code == 200
        blocked = client.post(
            f"/api/v1/documents/{approved['id']}/purge",
            headers=auth("admin"),
            json={"reason": "Solicitação de descarte."},
        )
        assert blocked.status_code == 409

        client.patch(
            f"/api/v1/documents/{approved['id']}/retention",
            headers=auth("admin"),
            json={"legal_hold": False, "reason": "Fim da preservação interna."},
        )
        purged = client.post(
            f"/api/v1/documents/{approved['id']}/purge",
            headers=auth("admin"),
            json={"reason": "Prazo e finalidade encerrados."},
        )
        assert purged.status_code == 200
        assert purged.json()["status"] == "PURGED"
        assert purged.json()["file_hash"] is None

        events = client.get(
            f"/api/v1/audit?document_id={approved['id']}",
            headers=auth("admin"),
        )
        assert events.status_code == 200
        actions = {event["action"] for event in events.json()}
        assert {"DOCUMENT_RECEIVED", "REVIEW_CLAIMED", "REVIEW_SUBMITTED", "DOCUMENT_PURGED"} <= actions

        spa = client.get("/")
        assert spa.status_code == 200
        assert "DOC Intelligence" in spa.text
        assert "Triagem ativa" in spa.text
        assert "Detalhes do documento" in spa.text
        assert 'type="file" multiple' in spa.text
        login = client.get("/login")
        assert login.status_code == 200
        assert "Bem-vindo ao" in login.text
        assert "handleLogout" in login.text


def awaitable_storage_count(client: TestClient) -> int:
    return len(client.app.state.storage.objects)


def test_evaluation_metrics_by_field_and_version_inputs() -> None:
    metrics = compute_metrics(
        [
            {
                "expected": {
                    "document_type": "RG",
                    "extracted_data": {"nome": "MARIA SILVA", "numero": "123"},
                },
                "predicted": {
                    "document_type": "RG",
                    "extracted_data": {"nome": "Maria Silva", "numero": "123"},
                    "confidence_score": 0.92,
                },
            },
            {
                "expected": {
                    "document_type": "CPF",
                    "extracted_data": {"nome": "JOAO SOUZA", "cpf": "11122233344"},
                },
                "predicted": {
                    "document_type": "RG",
                    "extracted_data": {"nome": "JOAO SOUZA", "cpf": "11122233300"},
                    "confidence_score": 0.70,
                },
            },
        ],
        threshold=0.85,
    )
    assert metrics["document_type_accuracy"] == 0.5
    assert metrics["field_accuracy"]["nome"] == 1.0
    assert metrics["field_accuracy"]["cpf"] == 0.0
    assert metrics["false_accept_rate"] == 0.0
    assert metrics["review_recall_on_errors"] == 1.0


def test_prompt_v2_strict_result_contract() -> None:
    result = ExtractionResult.model_validate(
        {
            "document_type": "CERTIDAO_CASAMENTO",
            "confidence_score": 0.91,
            "suggested_filename": "CERTIDAO_CASAMENTO_João_Da_Silva.pdf",
            "extracted_data": {
                "nome": "JOÃO DA SILVA",
                "nome_mae": None,
                "nome_pai": None,
                "cpf": "123.456.789-00",
                "numero_rg": None,
                "naturalidade": "Fortaleza - CE",
                "data_nascimento": "01/02/1990",
                "data_casamento": "10/06/2020",
                "orgao_emissor": None,
            },
        }
    )

    assert result.filename_suggested == "CERTIDAO_CASAMENTO_JOAO_DA_SILVA"
    assert validate_prompt_contract(result, "document_extraction_v2") is result
    assert add_filename_extension(result.filename_suggested, "application/pdf") == (
        "CERTIDAO_CASAMENTO_JOAO_DA_SILVA.pdf"
    )
    assert set(result.extracted_data) == {
        "nome",
        "nome_mae",
        "nome_pai",
        "cpf",
        "numero_rg",
        "naturalidade",
        "data_nascimento",
        "data_casamento",
        "orgao_emissor",
    }

    legacy = ExtractionResult.model_validate(
        {
            "document_type": "RG",
            "confidence_score": 0.80,
            "filename_suggested": "rg_legado.jpg",
            "extracted_data": {
                "nome": None,
                "cpf": None,
                "rg": "1234567",
                "data_nascimento": None,
            },
        }
    )
    assert legacy.filename_suggested == "RG_LEGADO"


class FakeOpenAIClient:
    def __init__(self, content: str) -> None:
        self.last_request: dict | None = None

        async def create(**kwargs):
            self.last_request = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_openai_vision_strategy_sends_base64_and_parses_json() -> None:
    client = FakeOpenAIClient(
        json.dumps(
            {
                "document_type": "CNH",
                "confidence_score": 0.93,
                "suggested_filename": "CNH_PESSOA_TESTE",
                "extracted_data": {
                    "nome": "PESSOA TESTE",
                    "nome_mae": None,
                    "nome_pai": None,
                    "cpf": "000.000.000-00",
                    "numero_rg": None,
                    "naturalidade": "CIDADE - UF",
                    "data_nascimento": "01/01/1990",
                    "data_casamento": None,
                    "orgao_emissor": "DETRAN",
                },
            }
        )
    )
    strategy = OpenAIVisionStrategy(client=client)

    document_type, extracted_data, confidence, suggested = asyncio.run(
        strategy.extract_file_bytes(
            document_id="documento-tecnico-1",
            file_bytes=make_png((20, 184, 205)),
            mime_type="image/png",
        )
    )

    assert document_type == "CNH"
    assert extracted_data["orgao_emissor"] == "DETRAN"
    assert confidence == 0.93
    assert suggested == "CNH_PESSOA_TESTE"
    assert client.last_request is not None
    assert client.last_request["model"] == "gpt-4o"
    assert client.last_request["response_format"] == {"type": "json_object"}
    image_url = client.last_request["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")


def test_openai_vision_strategy_invalid_json_forces_review() -> None:
    strategy = OpenAIVisionStrategy(client=FakeOpenAIClient("resposta inválida"))
    document_type, extracted_data, confidence, suggested = asyncio.run(
        strategy.extract_file_bytes(
            document_id="documento-tecnico-2",
            file_bytes=make_png((10, 20, 30)),
            mime_type="image/png",
        )
    )

    assert document_type == "OUTROS"
    assert all(value is None for value in extracted_data.values())
    assert confidence == 0.0
    assert suggested.startswith("OUTROS_DOCUMENTO_")


def test_pdf_first_page_is_rendered_as_png_for_vision() -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=320, height=200)
    page.insert_text((40, 80), "DOCUMENTO DE TESTE")
    pdf_bytes = pdf.tobytes()
    pdf.close()

    image_bytes, mime_type = prepare_openai_media(pdf_bytes, "application/pdf")

    assert mime_type == "image/png"
    assert image_bytes.startswith(b"\x89PNG")
