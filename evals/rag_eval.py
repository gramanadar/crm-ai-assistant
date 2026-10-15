"""Offline RAG evaluation. Uses the configured provider; the default fake provider needs no API key."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docx import Document

from app.config import get_settings
from app.db.session import get_sessionmaker, init_db, reset_engine
from app.llm.factory import RecordingProvider, build_provider
from app.rag.answer import ask_question
from app.rag.embed import build_embedder
from app.rag.ingest import ingest_upload
from app.rag.retrieve import search_chunks

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "datasets" / "rag_questions.json"
HANDBOOK = ROOT / "evals" / "fixtures" / "handbook.md"
REPORT = ROOT / "evals" / "reports" / "rag_report.json"


def build_docx(markdown: str) -> bytes:
    document = Document()
    for line in markdown.splitlines():
        if line.startswith("# "):
            document.add_paragraph(line[2:].strip(), style="Heading 1")
        elif line.strip():
            document.add_paragraph(line.strip())
    from io import BytesIO

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def run() -> dict:
    questions = json.loads(DATASET.read_text())
    session = get_sessionmaker()()
    embedder = build_embedder()
    provider = RecordingProvider(build_provider(), session)
    ingest_upload(session, embedder, "handbook.docx", build_docx(HANDBOOK.read_text()), "Employee handbook")
    session.commit()
    hits = 0
    answered_correct = 0
    citation_ok = 0
    citation_total = 0
    refusal_hits = 0
    refusal_total = 0
    rows = []
    for item in questions:
        vector = embedder.embed([item["question"]])[0]
        retrieved = search_chunks(session, item["question"], vector, embedder.model_name)
        expected = (item.get("expected_passage") or "").lower()
        hit = bool(expected) and any(expected in chunk.text.lower() for chunk in retrieved[:8])
        if item["expected_status"] == "answered":
            hits += int(hit)
        result = ask_question(session, provider, embedder, item["question"], f"eval_{item['id']}")
        session.commit()
        status_ok = result["status"] == item["expected_status"]
        if item["expected_status"] == "answered":
            answered_correct += int(status_ok and hit)
        if item["expected_status"] == "not_found":
            refusal_total += 1
            refusal_hits += int(result["status"] == "not_found" and not result["answer"])
        if result["status"] in {"answered", "partial"}:
            for citation in result["citations"]:
                citation_total += 1
                citation_ok += int(bool(citation["text"] and citation["chunk_id"]))
        rows.append({"id": item["id"], "expected": item["expected_status"], "actual": result["status"], "hit": hit})
    answered_total = sum(1 for item in questions if item["expected_status"] == "answered")
    report = {
        "questions": len(questions),
        "retrieval_hit_rate": round(hits / answered_total, 3) if answered_total else 0,
        "answer_correctness": round(answered_correct / answered_total, 3) if answered_total else 0,
        "citation_accuracy": round(citation_ok / citation_total, 3) if citation_total else 0,
        "refusal_accuracy": round(refusal_hits / refusal_total, 3) if refusal_total else 0,
        "rows": rows,
        "grader": "status-and-passage rubric; set LLM_PROVIDER to grade with a live model",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    session.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="sqlite:///./data/rag-eval.db")
    args = parser.parse_args()
    get_settings.cache_clear()
    import os

    os.environ["DATABASE_URL"] = args.database_url
    get_settings.cache_clear()
    reset_engine()
    init_db()
    report = run()
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
