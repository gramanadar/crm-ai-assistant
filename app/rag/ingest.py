"""Upload, extract, chunk, and embed a document."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Chunk, Document
from app.errors import AppError
from app.rag.chunk import ChunkConfig, chunk_blocks
from app.rag.embed import Embedder
from app.rag.extract import extract_document
from app.rag.retrieve import store_pgvector

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
ACTIVE_STATUSES = {"processing", "ready", "ready_with_warnings"}


def ingest_upload(
    session: Session,
    embedder: Embedder,
    filename: str,
    data: bytes,
    title: str | None = None,
) -> tuple[Document, bool]:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise AppError("validation_failed", "Only PDF and DOCX files can be uploaded.", 400)
    if not data:
        raise AppError("validation_failed", "The uploaded file is empty.", 400)

    digest = hashlib.sha256(data).hexdigest()
    existing = session.scalar(select(Document).where(Document.sha256 == digest))
    if existing is not None:
        return existing, True

    active = session.scalar(
        select(func.count()).select_from(Document).where(Document.status.in_(ACTIVE_STATUSES))
    )
    if (active or 0) >= get_settings().max_documents:
        raise AppError(
            "document_limit_reached",
            f"The library already has {get_settings().max_documents} active documents.",
            409,
        )

    settings = get_settings()
    uploads = Path(settings.data_dir) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    storage_path = uploads / digest
    storage_path.write_bytes(data)

    document = Document(
        id=str(uuid4()),
        title=(title or Path(filename).stem).strip() or Path(filename).stem,
        file_name=filename,
        sha256=digest,
        storage_path=str(storage_path),
        status="processing",
        extraction_status="pending",
        index_status="not_started",
        warnings=[],
    )
    session.add(document)
    session.flush()
    _process_document(session, document, data, embedder)
    return document, False


def reindex_document(session: Session, embedder: Embedder, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise AppError("not_found", "Document not found.", 404)
    path = Path(document.storage_path)
    if not path.exists():
        document.status = "failed"
        document.index_status = "failed"
        document.error_detail = "Stored file is missing, so the document cannot be re-indexed."
        session.flush()
        return document
    _delete_chunks(session, document.id)
    _process_document(session, document, path.read_bytes(), embedder)
    return document


def reembed_if_model_changed(session: Session, embedder: Embedder) -> None:
    stale_ids = session.scalars(
        select(Document.id).where(
            Document.index_status == "succeeded",
            Document.embedding_model.is_not(None),
            Document.embedding_model != embedder.model_name,
        )
    ).all()
    for document_id in stale_ids:
        reindex_document(session, embedder, document_id)


def document_payload(document: Document, duplicate: bool = False) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "file_name": document.file_name,
        "sha256": document.sha256,
        "status": document.status,
        "extraction_status": document.extraction_status,
        "index_status": document.index_status,
        "page_count": document.page_count,
        "char_count": document.char_count,
        "chunk_count": document.chunk_count,
        "warnings": document.warnings or [],
        "error_detail": document.error_detail,
        "embedding_model": document.embedding_model,
        "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
        "duplicate": duplicate,
        "report": {
            "pages": document.page_count,
            "characters": document.char_count,
            "chunks": document.chunk_count,
            "warnings": document.warnings or [],
        },
    }


def _process_document(session: Session, document: Document, data: bytes, embedder: Embedder) -> None:
    try:
        extracted = extract_document(document.file_name, data)
    except Exception as exc:
        document.extraction_status = "failed"
        document.index_status = "not_started"
        document.status = "failed"
        document.error_detail = f"Could not extract text: {exc.__class__.__name__}"
        document.warnings = []
        session.flush()
        return

    document.extraction_status = "succeeded"
    document.page_count = extracted.page_count
    document.char_count = extracted.char_count
    document.warnings = list(extracted.warnings)
    document.error_detail = None
    config = ChunkConfig.from_settings()
    drafts = chunk_blocks(extracted.blocks, config)
    document.index_status = "pending"
    session.flush()
    try:
        vectors = embedder.embed([draft.embed_text for draft in drafts]) if drafts else []
    except Exception as exc:
        document.index_status = "failed"
        document.status = "failed"
        document.embedding_model = None
        document.chunk_count = 0
        document.error_detail = f"Embedding failed: {exc.__class__.__name__}"
        session.flush()
        return

    for draft, vector in zip(drafts, vectors):
        chunk = Chunk(
            id=str(uuid4()),
            document_id=document.id,
            heading_path=draft.heading_path,
            page_from=draft.page_from,
            page_to=draft.page_to,
            chunk_index=draft.chunk_index,
            text=draft.text,
            embed_text=draft.embed_text,
            embedding=vector,
            embedding_model=embedder.model_name,
        )
        session.add(chunk)
        session.flush()
        store_pgvector(session, chunk.id, vector)

    document.chunk_count = len(drafts)
    document.embedding_model = embedder.model_name
    document.index_status = "succeeded"
    document.status = "ready_with_warnings" if document.warnings else "ready"
    session.flush()


def _delete_chunks(session: Session, document_id: str) -> None:
    for chunk in session.scalars(select(Chunk).where(Chunk.document_id == document_id)).all():
        session.delete(chunk)
    session.flush()
