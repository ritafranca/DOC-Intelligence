from __future__ import annotations

import io
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_DISABLED"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test-production-foundation.db"
os.environ["DATA_DIR"] = "./data/test-storage"
os.environ["EXTRACTOR_STRATEGY"] = "mock"

from fastapi.testclient import TestClient
from PIL import Image

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

        assert "DOC Intelligence" in client.get("/").text


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
