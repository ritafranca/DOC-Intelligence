from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app.extractor import ExtractionResult, build_extractor, load_prompt
from app.models import EvaluationRun, RunStatus


def normalize(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def compute_metrics(results: list[dict], threshold: float) -> dict:
    if not results:
        raise ValueError("O dataset de avaliação não contém casos.")
    type_hits = 0
    confidence_sum = 0.0
    field_stats: dict[str, dict[str, int]] = {}
    accepted = 0
    incorrect = 0
    false_accepts = 0
    incorrect_reviewed = 0

    for item in results:
        expected = item["expected"]
        predicted = item["predicted"]
        type_match = normalize(expected["document_type"]) == normalize(predicted["document_type"])
        type_hits += int(type_match)
        confidence = float(predicted["confidence_score"])
        confidence_sum += confidence
        is_accepted = confidence >= threshold
        accepted += int(is_accepted)

        fields_correct = True
        expected_fields = expected.get("extracted_data", {})
        predicted_fields = predicted.get("extracted_data", {})
        for field in sorted(set(expected_fields) | set(predicted_fields)):
            stats = field_stats.setdefault(field, {"correct": 0, "total": 0})
            match = normalize(expected_fields.get(field)) == normalize(predicted_fields.get(field))
            stats["correct"] += int(match)
            stats["total"] += 1
            fields_correct = fields_correct and match

        case_correct = type_match and fields_correct
        if not case_correct:
            incorrect += 1
            false_accepts += int(is_accepted)
            incorrect_reviewed += int(not is_accepted)

    total = len(results)
    return {
        "total_cases": total,
        "document_type_accuracy": round(type_hits / total, 4),
        "field_accuracy": {
            field: round(values["correct"] / values["total"], 4)
            for field, values in sorted(field_stats.items())
        },
        "average_confidence": round(confidence_sum / total, 4),
        "straight_through_rate": round(accepted / total, 4),
        "false_accept_rate": round(false_accepts / incorrect, 4) if incorrect else 0.0,
        "review_recall_on_errors": round(incorrect_reviewed / incorrect, 4) if incorrect else 1.0,
        "review_threshold": threshold,
    }


async def evaluate(dataset_path: Path, persist: bool = False) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = dataset.get("cases", [])
    extractor = build_extractor()
    prompt_version = getattr(extractor, "prompt_version", settings.prompt_version)
    prompt = load_prompt(prompt_version)
    results = []

    for case in cases:
        file_path = (dataset_path.parent / case["file"]).resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"Amostra não encontrada: {file_path}")
        result: ExtractionResult = await extractor.extract(
            document_id=f"eval-{case['id']}",
            file_path=file_path,
            mime_type=case["mime_type"],
            original_filename=file_path.name,
            prompt=prompt,
        )
        results.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "predicted": result.model_dump(),
            }
        )

    metrics = compute_metrics(results, settings.review_threshold)
    report = {
        "dataset_version": dataset["version"],
        "strategy": extractor.strategy_name,
        "model_version": extractor.model_version,
        "prompt_version": prompt_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }
    if persist:
        async with SessionLocal() as session:
            session.add(
                EvaluationRun(
                    dataset_version=dataset["version"],
                    strategy=extractor.strategy_name,
                    model_version=extractor.model_version,
                    prompt_version=prompt_version,
                    status=RunStatus.SUCCEEDED,
                    total_cases=len(results),
                    metrics=metrics,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia extração por campo/modelo/prompt.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--persist", action="store_true", help="Registra o relatório no PostgreSQL.")
    parser.add_argument("--output", type=Path, help="Grava o relatório agregado em JSON.")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.dataset.resolve(), persist=args.persist))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
