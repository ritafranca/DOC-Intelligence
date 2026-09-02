from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from PIL import Image


os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_DISABLED"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test-production-foundation.db"
os.environ["DATA_DIR"] = "./data/test-storage"
os.environ["EXTRACTOR_STRATEGY"] = "mock"

from scripts.evaluate_model import calculate_metrics, evaluate_dataset
from scripts.generate_mock_dataset import generate_dataset
from app.queue import MemoryDocumentQueue
from app.storage import LocalDemoObjectStorage


def test_generate_mock_dataset_has_matching_image_and_v2_truth(tmp_path: Path) -> None:
    template = tmp_path / "rg_blank.jpg"
    Image.new("RGB", (900, 570), "#d8f2cf").save(template, format="JPEG")
    images_dir = tmp_path / "images"
    truth_dir = tmp_path / "ground_truth"

    generated = generate_dataset(
        template_path=template,
        images_dir=images_dir,
        ground_truth_dir=truth_dir,
        count=2,
        seed=42,
        apply_distortion=False,
    )

    assert len(generated) == 2
    for image_path, truth_path in generated:
        assert image_path.is_file()
        assert truth_path.is_file()
        with Image.open(image_path) as image:
            assert image.format == "JPEG"
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        assert set(truth) == {
            "document_type",
            "confidence_score",
            "suggested_filename",
            "extracted_data",
        }
        assert truth["document_type"] == "RG"
        assert truth["suggested_filename"].startswith("RG_")
        assert truth["extracted_data"]["data_casamento"] is None
        assert truth["extracted_data"]["cpf"]
        assert truth["extracted_data"]["numero_rg"]


class EchoGoldenExtractor:
    strategy_name = "test-echo"
    model_version = "test-model"
    prompt_version = "document_extraction_v2"

    def __init__(self, truth_dir: Path) -> None:
        self.truth_dir = truth_dir

    async def extract_file_bytes(self, *, document_id: str, file_bytes: bytes, mime_type: str):
        stem = document_id.removeprefix("eval-")
        expected = json.loads((self.truth_dir / f"{stem}.json").read_text(encoding="utf-8"))
        return (
            expected["document_type"],
            expected["extracted_data"],
            0.99,
            expected["suggested_filename"],
        )


def test_evaluate_dataset_reports_accuracy_by_field_without_api(tmp_path: Path) -> None:
    template = tmp_path / "rg_blank.jpg"
    Image.new("RGB", (900, 570), "#d8f2cf").save(template, format="JPEG")
    images_dir = tmp_path / "images"
    truth_dir = tmp_path / "ground_truth"
    generate_dataset(
        template_path=template,
        images_dir=images_dir,
        ground_truth_dir=truth_dir,
        count=2,
        seed=7,
        apply_distortion=False,
    )

    report = asyncio.run(
        evaluate_dataset(
            images_dir=images_dir,
            ground_truth_dir=truth_dir,
            concurrency=2,
            extractor=EchoGoldenExtractor(truth_dir),
        )
    )

    assert report["metrics"]["document_type_accuracy"] == 1.0
    assert report["metrics"]["exact_case_accuracy"] == 1.0
    assert report["metrics"]["fields"]["cpf"]["accuracy"] == 1.0


def test_calculate_metrics_detects_incorrect_cpf() -> None:
    expected_fields = {
        "nome": "PESSOA TESTE",
        "nome_mae": "MAE TESTE",
        "nome_pai": "PAI TESTE",
        "cpf": "111.222.333-44",
        "numero_rg": "12.345.678-9",
        "naturalidade": "VITORIA - ES",
        "data_nascimento": "01/01/1990",
        "data_casamento": None,
        "orgao_emissor": "SSP/ES",
    }
    predicted_fields = dict(expected_fields, cpf="111.222.333-00")
    metrics = calculate_metrics(
        [
            {
                "expected": {
                    "document_type": "RG",
                    "suggested_filename": "RG_PESSOA_TESTE",
                    "extracted_data": expected_fields,
                },
                "predicted": {
                    "document_type": "RG",
                    "suggested_filename": "RG_PESSOA_TESTE",
                    "confidence_score": 0.90,
                    "extracted_data": predicted_fields,
                },
            }
        ]
    )

    assert metrics["fields"]["cpf"]["accuracy"] == 0.0
    assert metrics["fields"]["nome"]["accuracy"] == 1.0
    assert metrics["exact_case_accuracy"] == 0.0


def test_demo_queue_and_local_storage_persist_between_instances(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "source.jpg"
        source.write_bytes(b"synthetic-image-bytes")
        storage_root = tmp_path / "objects"
        first_storage = LocalDemoObjectStorage(storage_root)
        await first_storage.start()
        await first_storage.put_file("documents/aa/document.jpg", source, "image/jpeg")

        second_storage = LocalDemoObjectStorage(storage_root)
        await second_storage.start()
        assert await second_storage.get_bytes("documents/aa/document.jpg") == source.read_bytes()

        queue = MemoryDocumentQueue()
        await queue.start()
        await queue.enqueue_outbox("event-1", "document-1")
        assert await asyncio.wait_for(queue.dequeue(), timeout=1) == ("event-1", "document-1")
        queue.task_done()

    asyncio.run(scenario())
