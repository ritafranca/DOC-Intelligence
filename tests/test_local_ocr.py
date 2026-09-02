from __future__ import annotations

import asyncio
import io
import os
import threading
from pathlib import Path

from PIL import Image


os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_DISABLED"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test-production-foundation.db"
os.environ["DATA_DIR"] = "./data/test-storage"
os.environ["EXTRACTOR_STRATEGY"] = "mock"

from app.extractor import LocalOCRStrategy
from app.local_ocr import (
    LocalPipelineResult,
    OCRLine,
    calculate_confidence,
    classify_document,
    extract_fields,
)


RG_LINES = [
    OCRLine("CARTEIRA DE IDENTIDADE", 0.98),
    OCRLine("REGISTRO GERAL", 0.96),
    OCRLine("12.345.678-X", 0.95),
    OCRLine("NOME", 0.98),
    OCRLine("MARIA DE TESTE SILVA", 0.97),
    OCRLine("FILIAÇÃO — MÃE", 0.93),
    OCRLine("ANA DE TESTE SILVA", 0.95),
    OCRLine("FILIAÇÃO — PAI", 0.93),
    OCRLine("JOAO DE TESTE SILVA", 0.95),
    OCRLine("NATURALIDADE", 0.92),
    OCRLine("VITORIA - ES", 0.94),
    OCRLine("DATA DE NASCIMENTO", 0.97),
    OCRLine("01/02/1990", 0.98),
    OCRLine("CPF", 0.98),
    OCRLine("123.456.789-00", 0.99),
    OCRLine("ÓRGÃO EMISSOR", 0.94),
    OCRLine("SSP/ES", 0.95),
]


def test_local_heuristics_classify_and_extract_rg_fields() -> None:
    document_type = classify_document(RG_LINES)
    fields = extract_fields(RG_LINES, document_type)
    confidence = calculate_confidence(RG_LINES, document_type, fields)

    assert document_type == "RG"
    assert fields == {
        "nome": "MARIA DE TESTE SILVA",
        "nome_mae": "ANA DE TESTE SILVA",
        "nome_pai": "JOAO DE TESTE SILVA",
        "cpf": "123.456.789-00",
        "numero_rg": "12.345.678-X",
        "naturalidade": "VITORIA - ES",
        "data_nascimento": "01/02/1990",
        "data_casamento": None,
        "orgao_emissor": "SSP/ES",
    }
    assert confidence >= 0.85


def test_missing_critical_field_forces_human_review() -> None:
    lines = [line for line in RG_LINES if "CPF" not in line.text and "123.456" not in line.text]
    document_type = classify_document(lines)
    fields = extract_fields(lines, document_type)
    confidence = calculate_confidence(lines, document_type, fields)

    assert fields["cpf"] is None
    assert confidence < 0.85


def test_missing_rg_filiation_forces_human_review() -> None:
    lines = [
        line
        for line in RG_LINES
        if line.text not in {"FILIAÇÃO — MÃE", "ANA DE TESTE SILVA", "FILIAÇÃO — PAI", "JOAO DE TESTE SILVA"}
    ]
    document_type = classify_document(lines)
    fields = extract_fields(lines, document_type)

    assert fields["nome"] is not None
    assert fields["cpf"] is not None
    assert fields["nome_mae"] is None
    assert fields["nome_pai"] is None
    assert calculate_confidence(lines, document_type, fields) < 0.85


def test_heuristics_tolerate_common_ocr_errors_in_labels() -> None:
    lines = [
        OCRLine("REGISEBAGERAL", 0.81),
        OCRLine("724056312", 0.90),
        OCRLine("NOME", 0.95),
        OCRLine("PESSOA DE TESTE", 0.93),
        OCRLine("FILIASRORMBE", 0.78),
        OCRLine("ANA DE TESTE", 0.92),
        OCRLine("FILIABBOBPAI", 0.79),
        OCRLine("JOAO DE TESTE", 0.91),
        OCRLine("CPF", 0.96),
        OCRLine("123.456.789-00", 0.96),
    ]

    document_type = classify_document(lines)
    fields = extract_fields(lines, document_type)

    assert document_type == "RG"
    assert fields["numero_rg"] == "724056312"
    assert fields["nome_mae"] == "ANA DE TESTE"
    assert fields["nome_pai"] == "JOAO DE TESTE"


def test_rg_layout_recovers_filiation_when_labels_are_illegible() -> None:
    lines = [
        OCRLine("REGISTRO GERAL", 0.92),
        OCRLine("12.345.678-X", 0.93),
        OCRLine("NOME", 0.96),
        OCRLine("PESSOA DE TESTE", 0.95),
        OCRLine("F1L1A??0", 0.40),
        OCRLine("ANA DE TESTE", 0.91),
        OCRLine("F1L1A??0", 0.41),
        OCRLine("JOAO DE TESTE", 0.90),
        OCRLine("NATURALIDADE", 0.94),
        OCRLine("VITORIA - ES", 0.92),
        OCRLine("CPF", 0.96),
        OCRLine("123.456.789-00", 0.97),
    ]

    fields = extract_fields(lines, "RG")

    assert fields["nome_mae"] == "ANA DE TESTE"
    assert fields["nome_pai"] == "JOAO DE TESTE"


class FakeLocalPipeline:
    model_version = "fake-local-ocr"

    def __init__(self) -> None:
        self.thread_id: int | None = None

    def extract(self, **_kwargs) -> LocalPipelineResult:
        self.thread_id = threading.get_ident()
        return LocalPipelineResult(
            document_type="RG",
            extracted_data={
                "nome": "PESSOA TESTE",
                "nome_mae": None,
                "nome_pai": None,
                "cpf": "123.456.789-00",
                "numero_rg": "12.345.678-X",
                "naturalidade": None,
                "data_nascimento": "01/01/1990",
                "data_casamento": None,
                "orgao_emissor": "SSP/ES",
            },
            confidence_score=0.91,
            suggested_filename="RG_PESSOA_TESTE",
        )


def test_local_strategy_runs_cpu_pipeline_outside_event_loop(tmp_path: Path) -> None:
    image_path = tmp_path / "rg.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="JPEG")
    image_path.write_bytes(buffer.getvalue())
    pipeline = FakeLocalPipeline()
    strategy = LocalOCRStrategy(pipeline=pipeline)
    event_loop_thread = threading.get_ident()

    result = asyncio.run(
        strategy.extract(
            document_id="doc-local-1",
            file_path=image_path,
            mime_type="image/jpeg",
            original_filename=image_path.name,
            prompt="",
        )
    )

    assert pipeline.thread_id is not None
    assert pipeline.thread_id != event_loop_thread
    assert result.document_type == "RG"
    assert result.extracted_data["cpf"] == "123.456.789-00"
