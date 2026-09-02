from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from app.config import settings  # noqa: E402
from app.extractor import (  # noqa: E402
    OPENAI_PROMPT_VERSION,
    V2_EXTRACTED_FIELD_ORDER,
    ExtractionResult,
    OpenAIVisionStrategy,
    validate_prompt_contract,
)
from app.local_ocr import LocalOCRPipeline  # noqa: E402


DEFAULT_IMAGES_DIR = ROOT_DIR / "tests" / "dataset" / "images"
DEFAULT_GROUND_TRUTH_DIR = ROOT_DIR / "tests" / "dataset" / "ground_truth"
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}


class VisionEvaluator(Protocol):
    strategy_name: str
    model_version: str

    async def extract_file_bytes(
        self,
        *,
        document_id: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, dict[str, str | None], float, str]: ...


class LocalOCREvaluator:
    strategy_name = "local"
    prompt_version = "local_ocr_rules_v1"

    def __init__(self) -> None:
        self.pipeline = LocalOCRPipeline(
            lang=settings.local_ocr_lang,
            cpu_threads=settings.local_ocr_cpu_threads,
            poppler_path=settings.local_ocr_poppler_path,
        )
        self.model_version = self.pipeline.model_version

    async def extract_file_bytes(
        self,
        *,
        document_id: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, dict[str, str | None], float, str]:
        result = await asyncio.to_thread(
            self.pipeline.extract,
            document_id=document_id,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )
        return (
            result.document_type,
            result.extracted_data,
            result.confidence_score,
            result.suggested_filename,
        )


def _canonical(value: Any) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", str(value)).strip()
    return " ".join(normalized.split()).casefold()


def _matches(expected: Any, predicted: Any) -> bool:
    return _canonical(expected) == _canonical(predicted)


def _load_cases(images_dir: Path, ground_truth_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not images_dir.is_dir():
        raise FileNotFoundError(f"Diretório de imagens não encontrado: {images_dir}")
    if not ground_truth_dir.is_dir():
        raise FileNotFoundError(f"Diretório de gabaritos não encontrado: {ground_truth_dir}")

    image_paths = sorted(
        path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not image_paths:
        raise ValueError(f"Nenhuma imagem de avaliação encontrada em {images_dir}")

    cases: list[tuple[Path, dict[str, Any]]] = []
    for image_path in image_paths:
        truth_path = ground_truth_dir / f"{image_path.stem}.json"
        if not truth_path.is_file():
            raise FileNotFoundError(f"Gabarito ausente para {image_path.name}: {truth_path}")
        expected = json.loads(truth_path.read_text(encoding="utf-8"))
        validated = validate_prompt_contract(
            ExtractionResult.model_validate(expected),
            OPENAI_PROMPT_VERSION,
        )
        expected["suggested_filename"] = validated.filename_suggested
        expected["extracted_data"] = validated.extracted_data
        cases.append((image_path, expected))
    return cases


def calculate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        raise ValueError("Não há resultados para calcular métricas.")

    field_stats = {
        field: {"correct": 0, "total": total, "expected_values": 0, "correct_expected_values": 0}
        for field in V2_EXTRACTED_FIELD_ORDER
    }
    type_correct = 0
    filename_correct = 0
    exact_cases = 0
    confidence_sum = 0.0
    forced_review_count = 0

    for result in results:
        expected = result["expected"]
        predicted = result["predicted"]
        type_match = _matches(expected["document_type"], predicted["document_type"])
        filename_match = _matches(
            expected["suggested_filename"], predicted["suggested_filename"]
        )
        type_correct += int(type_match)
        filename_correct += int(filename_match)
        confidence = float(predicted["confidence_score"])
        confidence_sum += confidence
        forced_review_count += int(confidence < settings.review_threshold)

        all_fields_match = True
        for field in V2_EXTRACTED_FIELD_ORDER:
            expected_value = expected["extracted_data"].get(field)
            predicted_value = predicted["extracted_data"].get(field)
            match = _matches(expected_value, predicted_value)
            stats = field_stats[field]
            stats["correct"] += int(match)
            if expected_value is not None:
                stats["expected_values"] += 1
                stats["correct_expected_values"] += int(match)
            all_fields_match = all_fields_match and match
        exact_cases += int(type_match and filename_match and all_fields_match)

    for stats in field_stats.values():
        stats["accuracy"] = round(stats["correct"] / stats["total"], 4)
        stats["populated_accuracy"] = (
            round(stats["correct_expected_values"] / stats["expected_values"], 4)
            if stats["expected_values"]
            else None
        )

    return {
        "total_cases": total,
        "document_type_accuracy": round(type_correct / total, 4),
        "suggested_filename_accuracy": round(filename_correct / total, 4),
        "exact_case_accuracy": round(exact_cases / total, 4),
        "average_confidence": round(confidence_sum / total, 4),
        "forced_review_count": forced_review_count,
        "review_threshold": settings.review_threshold,
        "fields": field_stats,
    }


async def evaluate_dataset(
    *,
    images_dir: Path = DEFAULT_IMAGES_DIR,
    ground_truth_dir: Path = DEFAULT_GROUND_TRUTH_DIR,
    concurrency: int = 2,
    strategy: str = "local",
    extractor: VisionEvaluator | None = None,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("A concorrência deve ser maior que zero.")
    cases = _load_cases(images_dir, ground_truth_dir)
    if extractor is None:
        if strategy == "local":
            extractor = LocalOCREvaluator()
        elif strategy == "openai":
            if not settings.openai_api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY não configurada. Defina a chave no ambiente ou no arquivo .env."
                )
            extractor = OpenAIVisionStrategy()
        else:
            raise ValueError(f"Estratégia de avaliação inválida: {strategy}")

    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(image_path: Path, expected: dict[str, Any]) -> dict[str, Any]:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        async with semaphore:
            file_bytes = await asyncio.to_thread(image_path.read_bytes)
            document_type, extracted_data, confidence, suggested_filename = (
                await extractor.extract_file_bytes(
                    document_id=f"eval-{image_path.stem}",
                    file_bytes=file_bytes,
                    mime_type=mime_type,
                )
            )
        return {
            "id": image_path.stem,
            "expected": expected,
            "predicted": {
                "document_type": document_type,
                "confidence_score": confidence,
                "suggested_filename": suggested_filename,
                "extracted_data": extracted_data,
            },
        }

    results = await asyncio.gather(
        *(evaluate_one(image_path, expected) for image_path, expected in cases)
    )
    prompt_version = getattr(extractor, "prompt_version", OPENAI_PROMPT_VERSION)
    return {
        "dataset": "synthetic-rg-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy": extractor.strategy_name,
        "model_version": extractor.model_version,
        "prompt_version": prompt_version,
        "metrics": calculate_metrics(results),
        "results": results,
    }


def print_terminal_report(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    print("\nDOC Intelligence — relatório de avaliação")
    print(f"Modelo: {report['model_version']} | Prompt: {report['prompt_version']}")
    print(f"Documentos avaliados: {metrics['total_cases']}")
    print(f"Tipo do documento: {metrics['document_type_accuracy']:.1%}")
    print(f"Nome sugerido: {metrics['suggested_filename_accuracy']:.1%}")
    print(f"Caso integralmente correto: {metrics['exact_case_accuracy']:.1%}")
    print("\nAcurácia exata por campo:")
    for field, stats in metrics["fields"].items():
        label = field.replace("_", " ").upper()
        print(f"  {label}: {stats['accuracy']:.1%} ({stats['correct']}/{stats['total']})")
    print(f"\nConfiança média: {metrics['average_confidence']:.3f}")
    print(
        "Encaminhados para revisão: "
        f"{metrics['forced_review_count']}/{metrics['total_cases']} "
        f"(limiar {metrics['review_threshold']:.0%})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Avalia a estratégia local ou OpenAI contra o dataset sintético."
    )
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--ground-truth-dir", type=Path, default=DEFAULT_GROUND_TRUTH_DIR)
    parser.add_argument(
        "--strategy",
        choices=("local", "openai"),
        default="local",
        help="Extrator avaliado; local não envia documentos a terceiros.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("EVAL_CONCURRENCY", "1")),
        help="Máximo de documentos simultâneos (use 1 para OCR local em CPU).",
    )
    parser.add_argument("--output", type=Path, help="Caminho opcional do relatório JSON.")
    parser.add_argument("--min-field-accuracy", type=float, default=0.0)
    parser.add_argument("--min-document-type-accuracy", type=float, default=0.0)
    args = parser.parse_args()

    try:
        report = asyncio.run(
            evaluate_dataset(
                images_dir=args.images_dir.resolve(),
                ground_truth_dir=args.ground_truth_dir.resolve(),
                concurrency=args.concurrency,
                strategy=args.strategy,
            )
        )
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Falha na avaliação: {exc}", file=sys.stderr)
        return 2
    print_terminal_report(report)
    if args.output:
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Relatório JSON: {output_path}")

    populated_field_accuracies = [
        stats["populated_accuracy"]
        for stats in report["metrics"]["fields"].values()
        if stats["populated_accuracy"] is not None
    ]
    minimum_field_accuracy = min(populated_field_accuracies, default=1.0)
    failed = (
        minimum_field_accuracy < args.min_field_accuracy
        or report["metrics"]["document_type_accuracy"] < args.min_document_type_accuracy
    )
    if failed:
        print("Quality gate reprovado.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
