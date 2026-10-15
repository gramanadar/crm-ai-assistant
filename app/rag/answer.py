"""Answer a question from retrieved chunks and check citations in the backend."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.llm.base import LLMProvider, StructuredResult
from app.rag.embed import Embedder
from app.rag.ingest import reembed_if_model_changed
from app.rag.prompts import ANSWER_SYSTEM, answer_user_message
from app.rag.retrieve import RetrievedChunk, search_chunks


class Claim(BaseModel):
    text: str
    chunk_ids: list[str] = Field(default_factory=list)


class AnswerPayload(BaseModel):
    status: str
    answer: str = ""
    claims: list[Claim] = Field(default_factory=list)
    missing_information: str = ""


ALLOWED_STATUS = {"answered", "partial", "not_found"}


def ask_question(
    session: Session,
    provider: LLMProvider,
    embedder: Embedder,
    question: str,
    request_id: str,
) -> dict:
    question = question.strip()
    if not question:
        from app.errors import AppError

        raise AppError("validation_failed", "A question is required.", 400)

    reembed_if_model_changed(session, embedder)
    vector = embedder.embed([question])[0]
    chunks = search_chunks(session, question, vector, embedder.model_name)
    if not chunks:
        return _not_found(
            "No indexed passage was similar enough to answer this question.",
            chunks=[],
        )

    messages = [{"role": "user", "content": answer_user_message(question, chunks)}]
    result = provider.structured(
        system=ANSWER_SYSTEM,
        messages=messages,
        schema=AnswerPayload,
        task="ask",
        request_id=request_id,
    )
    payload, failure = _validated(result, chunks)
    if failure:
        retry_messages = messages + [
            {"role": "user", "content": "The previous output was invalid. Return only the schema."}
        ]
        result = provider.structured(
            system=ANSWER_SYSTEM,
            messages=retry_messages,
            schema=AnswerPayload,
            task="ask",
            request_id=request_id,
        )
        payload, failure = _validated(result, chunks)
        if failure:
            return _not_found("The answer could not be validated against the documents.", chunks)

    assert payload is not None
    if _has_fabricated_citation(payload, chunks):
        retry_messages = messages + [
            {
                "role": "user",
                "content": "Cite only chunk ids from the supplied chunks. Do not invent ids.",
            }
        ]
        result = provider.structured(
            system=ANSWER_SYSTEM,
            messages=retry_messages,
            schema=AnswerPayload,
            task="ask",
            request_id=request_id,
        )
        payload, failure = _validated(result, chunks)
        if failure or payload is None or _has_fabricated_citation(payload, chunks):
            return _not_found("A citation did not match a retrieved passage.", chunks)

    return _finalize(payload, chunks)


def _validated(result: StructuredResult, chunks: list[RetrievedChunk]) -> tuple[AnswerPayload | None, str | None]:
    if result.stop_reason in {"refusal", "max_tokens"} or result.invalid or result.parsed is None:
        return None, result.stop_reason or "invalid"
    payload = result.parsed
    if not isinstance(payload, AnswerPayload):
        try:
            payload = AnswerPayload.model_validate(payload)
        except Exception:
            return None, "invalid"
    if payload.status not in ALLOWED_STATUS:
        return None, "status"
    if payload.status in {"partial", "not_found"} and not payload.missing_information.strip():
        return None, "missing_information"
    return payload, None


def _has_fabricated_citation(payload: AnswerPayload, chunks: list[RetrievedChunk]) -> bool:
    allowed = {chunk.id for chunk in chunks}
    for claim in payload.claims:
        for chunk_id in claim.chunk_ids:
            if chunk_id not in allowed:
                return True
    return False


def _finalize(payload: AnswerPayload, chunks: list[RetrievedChunk]) -> dict:
    allowed = {chunk.id for chunk in chunks}
    claims = []
    for claim in payload.claims:
        ids = [chunk_id for chunk_id in claim.chunk_ids if chunk_id in allowed]
        if ids and claim.text.strip():
            claims.append({"text": claim.text.strip(), "chunk_ids": ids})
    if payload.status == "answered" and not claims:
        return _not_found("The draft answer did not cite a retrieved passage.", chunks)
    if payload.status == "partial" and not claims:
        return _not_found(payload.missing_information or "The documents do not cover this question.", chunks)
    if payload.status == "not_found":
        return _not_found(payload.missing_information, chunks)
    citations = _citations(claims, chunks)
    return {
        "status": payload.status,
        "answer": payload.answer if payload.status != "not_found" else "",
        "claims": claims,
        "missing_information": payload.missing_information if payload.status != "answered" else "",
        "citations": citations,
    }


def _not_found(missing: str, chunks: list[RetrievedChunk]) -> dict:
    return {
        "status": "not_found",
        "answer": "",
        "claims": [],
        "missing_information": missing,
        "citations": [],
    }


def _citations(claims: list[dict], chunks: list[RetrievedChunk]) -> list[dict]:
    by_id = {chunk.id: chunk for chunk in chunks}
    citations = []
    emitted: set[str] = set()
    for claim in claims:
        for chunk_id in claim["chunk_ids"]:
            if chunk_id in emitted or chunk_id not in by_id:
                continue
            emitted.add(chunk_id)
            chunk = by_id[chunk_id]
            citations.append(
                {
                    "chunk_id": chunk.id,
                    "document_title": chunk.document_title,
                    "heading_path": chunk.heading_path,
                    "page_from": chunk.page_from,
                    "page_to": chunk.page_to,
                    "text": chunk.text,
                }
            )
    return citations
