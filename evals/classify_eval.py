"""Classification evaluation with a trade-off curve and a delta against the previous report."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.classify.service import classify_request
from app.config import get_settings
from app.db.session import get_sessionmaker, init_db, reset_engine
from app.llm.factory import RecordingProvider, build_provider

ROOT = Path(__file__).resolve().parents[1]
TEST_SET = ROOT / "evals" / "datasets" / "classify_test.json"
REPORT = ROOT / "evals" / "reports" / "classify_report.json"
PREVIOUS = ROOT / "evals" / "reports" / "classify_previous.json"


def _macro_f1(confusion: dict[str, dict[str, int]], labels: list[str]) -> float:
    scores = []
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        scores.append(0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return sum(scores) / len(scores) if scores else 0


def run() -> dict:
    rows = json.loads(TEST_SET.read_text())
    labels = get_settings().category_list
    session = get_sessionmaker()()
    provider = RecordingProvider(build_provider(), session)
    predictions = []
    confusion = {label: {other: 0 for other in labels} for label in labels}
    correct = 0
    for index, row in enumerate(rows):
        result = classify_request(session, provider, row["text"], "eval", f"classify_{index}")
        session.commit()
        predicted = result["category"] or "other"
        expected = row["category"]
        if predicted not in confusion:
            predicted = "other"
        confusion[expected][predicted] += 1
        correct += int(predicted == expected)
        predictions.append({"text": row["text"], "expected": expected, "predicted": predicted, "confidence": result["confidence"] or 0, "status": result["status"]})
    curve = []
    for threshold in (0.5, 0.6, 0.7, 0.75, 0.8, 0.9):
        auto = [row for row in predictions if row["confidence"] >= threshold and row["predicted"] != "other"]
        auto_correct = sum(1 for row in auto if row["predicted"] == row["expected"])
        curve.append(
            {
                "threshold": threshold,
                "review_rate": round(1 - (len(auto) / len(predictions)), 3) if predictions else 0,
                "auto_accuracy": round(auto_correct / len(auto), 3) if auto else None,
            }
        )
    report = {
        "accuracy": round(correct / len(rows), 3) if rows else 0,
        "macro_f1": round(_macro_f1(confusion, labels), 3),
        "confusion": confusion,
        "tradeoff_curve": curve,
        "chosen_threshold": get_settings().classify_confidence_threshold,
        "test_size": len(rows),
    }
    if PREVIOUS.exists():
        previous = json.loads(PREVIOUS.read_text())
        report["delta_vs_previous"] = {
            "accuracy": round(report["accuracy"] - previous.get("accuracy", 0), 3),
            "macro_f1": round(report["macro_f1"] - previous.get("macro_f1", 0), 3),
        }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    session.close()
    return report


def main() -> None:
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite:///./data/classify-eval.db")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
