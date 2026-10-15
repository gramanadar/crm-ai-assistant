"""Hybrid retrieval: vector similarity plus full-text, fused with RRF."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Chunk, Document

VECTOR_SQL = """
SELECT id, 1 - (embedding_vec <=> CAST(:query AS vector)) AS cosine
FROM chunks
WHERE embedding_model = :model AND embedding_vec IS NOT NULL
ORDER BY embedding_vec <=> CAST(:query AS vector)
LIMIT :limit
"""

FTS_SQL = """
SELECT id, ts_rank(tsv, plainto_tsquery('english', :query)) AS rank
FROM chunks
WHERE embedding_model = :model AND tsv @@ plainto_tsquery('english', :query)
ORDER BY rank DESC
LIMIT :limit
"""

_STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "of",
    "to",
    "and",
    "for",
    "in",
    "on",
    "what",
    "how",
    "does",
    "do",
    "i",
    "me",
    "my",
    "we",
    "our",
    "with",
    "from",
    "that",
    "this",
    "it",
    "be",
    "are",
    "can",
    "please",
}


@dataclass
class RetrievedChunk:
    id: str
    document_id: str
    document_title: str
    heading_path: str
    page_from: int | None
    page_to: int | None
    text: str
    vector_score: float | None
    keyword_score: float
    fused_score: float


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return [item_id for item_id, _score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]


def fuse_scores(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


def select_candidates(
    fused_ids: list[str],
    vector_scores: dict[str, float],
    keyword_scores: dict[str, float],
    top_k: int,
    similarity_floor: float,
) -> list[str]:
    chosen = []
    for chunk_id in fused_ids[:top_k]:
        vector_score = vector_scores.get(chunk_id)
        keyword_score = keyword_scores.get(chunk_id, 0.0)
        if keyword_score > 0 or (vector_score is not None and vector_score >= similarity_floor):
            chosen.append(chunk_id)
    return chosen


def tokenize(value: str) -> list[str]:
    tokens = []
    seen = set()
    for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", value.lower()):
        if token in _STOPWORDS:
            continue
        for variant in _token_variants(token):
            if variant not in seen:
                seen.add(variant)
                tokens.append(variant)
    return tokens


def _token_variants(token: str) -> list[str]:
    variants = [token]
    for suffix in ("s", "es", "ed", "ing"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            variants.append(token[: -len(suffix)])
    return variants


def keyword_score(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    haystack = text.lower()
    if query.strip().lower() in haystack:
        return 1.0
    text_tokens = set(tokenize(text))
    overlap = [token for token in query_tokens if token in text_tokens]
    if not overlap:
        return 0.0
    return len(overlap) / len(query_tokens)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def search_chunks(
    session: Session,
    query: str,
    query_vector: list[float],
    embedding_model: str,
    settings: Settings | None = None,
) -> list[RetrievedChunk]:
    settings = settings or get_settings()
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        vector_scores, keyword_scores = _search_postgres(session, query, query_vector, embedding_model)
    else:
        vector_scores, keyword_scores = _search_memory(session, query, query_vector, embedding_model)
    fused = fuse_scores(
        [
            _ranked(vector_scores),
            _ranked(keyword_scores),
        ],
        k=settings.rrf_k,
    )
    fused_ids = [item_id for item_id, _score in sorted(fused.items(), key=lambda pair: pair[1], reverse=True)]
    chosen = select_candidates(
        fused_ids,
        vector_scores,
        keyword_scores,
        settings.retrieval_top_k,
        settings.similarity_floor,
    )
    if not chosen:
        return []
    rows = session.execute(
        select(Chunk, Document.title)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(chosen))
    ).all()
    by_id = {chunk.id: (chunk, title) for chunk, title in rows}
    results = []
    for chunk_id in chosen:
        if chunk_id not in by_id:
            continue
        chunk, title = by_id[chunk_id]
        results.append(
            RetrievedChunk(
                id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                heading_path=chunk.heading_path,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
                text=chunk.text,
                vector_score=vector_scores.get(chunk_id),
                keyword_score=keyword_scores.get(chunk_id, 0.0),
                fused_score=fused.get(chunk_id, 0.0),
            )
        )
    return results


def _ranked(scores: dict[str, float]) -> list[str]:
    return [item_id for item_id, _score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]


def _search_memory(
    session: Session, query: str, query_vector: list[float], embedding_model: str
) -> tuple[dict[str, float], dict[str, float]]:
    chunks = session.scalars(select(Chunk).where(Chunk.embedding_model == embedding_model)).all()
    vector_scores: dict[str, float] = {}
    keyword_scores: dict[str, float] = {}
    for chunk in chunks:
        if chunk.embedding:
            score = cosine(query_vector, chunk.embedding)
            if score > 0:
                vector_scores[chunk.id] = score
        keyword = keyword_score(query, chunk.embed_text or chunk.text)
        if keyword > 0:
            keyword_scores[chunk.id] = keyword
    return vector_scores, keyword_scores


def _search_postgres(
    session: Session, query: str, query_vector: list[float], embedding_model: str
) -> tuple[dict[str, float], dict[str, float]]:
    literal = "[" + ",".join(f"{float(value):.8f}" for value in query_vector) + "]"
    vector_rows = session.execute(
        text(VECTOR_SQL),
        {"query": literal, "model": embedding_model, "limit": 20},
    ).mappings()
    keyword_rows = session.execute(
        text(FTS_SQL),
        {"query": query, "model": embedding_model, "limit": 20},
    ).mappings()
    vector_scores = {row["id"]: float(row["cosine"]) for row in vector_rows}
    keyword_scores = {row["id"]: float(row["rank"]) for row in keyword_rows if row["rank"]}
    return vector_scores, keyword_scores


def store_pgvector(session: Session, chunk_id: str, embedding: list[float]) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    literal = "[" + ",".join(f"{float(value):.8f}" for value in embedding) + "]"
    session.execute(
        text("UPDATE chunks SET embedding_vec = CAST(:embedding AS vector) WHERE id = :id"),
        {"embedding": literal, "id": chunk_id},
    )
