"""Tools the model may call. There is no CRM write tool."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.actions.service import create_draft
from app.llm.base import ToolSpec
from app.rag.embed import Embedder
from app.rag.retrieve import search_chunks

TOOL_SPECS = [
    ToolSpec(
        name="crm_lookup",
        description="Find a CRM contact or case by email or record id. Read-only.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "email": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["email", "record_id"],
        },
    ),
    ToolSpec(
        name="search_documents",
        description="Search company documents and return passages with chunk ids.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="draft_crm_action",
        description="Store a local CRM note or task draft. Does not contact the CRM write API.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": ["note", "task"]},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "record_id": {"type": "string"},
                "email": {"type": "string"},
                "due_date": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["kind", "title", "body", "record_id", "email", "due_date", "priority"],
        },
    ),
]


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = ""
    record_id: str = ""


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)


class DraftArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    title: str
    body: str
    record_id: str = ""
    email: str = ""
    due_date: str = ""
    priority: str = ""


def tool_names() -> list[str]:
    return [tool.name for tool in TOOL_SPECS]


def run_tool(name: str, arguments: dict, *, session, reader, embedder: Embedder, actor: str, conversation_id: str) -> dict:
    try:
        if name == "crm_lookup":
            args = LookupArgs.model_validate(arguments)
            if not args.email.strip() and not args.record_id.strip():
                return _error("email or record_id is required", "validation_failed")
            result = reader.lookup(email=args.email.strip() or None, record_id=args.record_id.strip() or None)
            if not result.ok:
                return {
                    "is_error": True,
                    "ok": False,
                    "code": result.code,
                    "message": result.message,
                    "summary": _lookup_summary(result.message),
                }
            record = result.record or {}
            return {
                "is_error": False,
                "ok": True,
                "id": record.get("id"),
                "name": record.get("name"),
                "email": record.get("email"),
                "case_title": record.get("case_title"),
                "summary": f"Looked up contact {record.get('name') or record.get('email')}… found",
            }
        if name == "search_documents":
            args = SearchArgs.model_validate(arguments)
            vector = embedder.embed([args.query])[0]
            chunks = search_chunks(session, args.query, vector, embedder.model_name)
            return {
                "is_error": False,
                "ok": True,
                "chunks": [
                    {
                        "chunk_id": chunk.id,
                        "document_title": chunk.document_title,
                        "heading_path": chunk.heading_path,
                        "page_from": chunk.page_from,
                        "page_to": chunk.page_to,
                        "text": chunk.text,
                    }
                    for chunk in chunks
                ],
                "summary": f"Searched documents… {len(chunks)} passages",
            }
        if name == "draft_crm_action":
            args = DraftArgs.model_validate(arguments)
            draft = create_draft(
                session,
                reader,
                kind=args.kind,
                title=args.title,
                body=args.body,
                record_id=args.record_id or None,
                email=args.email or None,
                due_date=args.due_date or None,
                priority=args.priority or None,
                actor=actor,
                conversation_id=conversation_id,
            )
            return {
                "is_error": False,
                "ok": True,
                "draft_id": draft["id"],
                "status": draft["status"],
                "summary": f"Drafted a CRM {draft['type']}",
                "draft": draft,
            }
        return _error("Unknown tool", "validation_failed")
    except ValidationError as exc:
        return _error(exc.errors()[0].get("msg", "Invalid tool arguments"), "validation_failed")
    except Exception as exc:
        code = getattr(exc, "code", "tool_failed")
        message = getattr(exc, "message", None) or str(exc)
        return _error(message, code)


def _error(message: str, code: str) -> dict:
    return {"is_error": True, "ok": False, "code": code, "message": message, "summary": message}


def _lookup_summary(message: str) -> str:
    if "No record found" in message:
        return "No record found"
    if message == "CRM unavailable" or "couldn't reach" in message:
        return "CRM unavailable"
    return message
