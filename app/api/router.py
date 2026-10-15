"""HTTP routes. Business rules stay in the service modules."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.executor import execute_action
from app.actions.service import (
    action_events,
    action_payload,
    approve_action,
    edit_action,
    list_actions,
    reject_action,
    submit_action,
)
from app.agent.loop import run_agent_turn
from app.auth import CurrentUser, require_user, user_from_header
from app.classify.service import classify_request, export_labels, list_requests, review_request
from app.config import get_settings
from app.crm.client import build_reader
from app.db.models import Document
from app.db.session import database_ready, get_sessionmaker
from app.errors import AppError
from app.llm.factory import RecordingProvider, build_provider
from app.logging_config import user_var
from app.rag.answer import ask_question
from app.rag.embed import build_embedder
from app.rag.ingest import document_payload, ingest_upload, reindex_document
from app.runtime import get_runtime
from app.usage.service import usage_csv, usage_report
from app.webhooks.dispatch import accept_n8n_result

router = APIRouter()


def get_db():
    factory = get_sessionmaker()
    db = factory()
    try:
        yield db
        db.commit()
    except AppError:
        db.commit()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_user(request: Request) -> CurrentUser:
    user = user_from_header(request.headers.get("authorization"))
    user_var.set(user.id)
    return user


def envelope(request: Request, data) -> dict:
    return {"request_id": request.state.request_id, "data": data}


class AskBody(BaseModel):
    question: str


class RequestBody(BaseModel):
    text: str
    source: str = "manual"


class ReviewBody(BaseModel):
    decision: str
    category: str | None = None


class AgentBody(BaseModel):
    message: str
    conversation_id: str | None = None


class EditBody(BaseModel):
    payload: dict


@router.get("/documents")
def get_documents(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    rows = db.scalars(select(Document).order_by(Document.uploaded_at.desc())).all()
    return envelope(request, {"documents": [document_payload(row) for row in rows]})


@router.post("/documents")
async def post_document(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_user),
):
    require_user(user, "approver")
    data = await file.read()
    document, duplicate = ingest_upload(db, build_embedder(), file.filename or "upload", data, title)
    return envelope(request, document_payload(document, duplicate))


@router.post("/documents/{document_id}/reindex")
def post_reindex(document_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    require_user(user, "approver")
    document = reindex_document(db, build_embedder(), document_id)
    return envelope(request, document_payload(document))


@router.post("/ask")
def post_ask(body: AskBody, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    provider = RecordingProvider(build_provider(), db)
    result = ask_question(db, provider, build_embedder(), body.question, request.state.request_id)
    return envelope(request, result)


@router.post("/requests")
def post_request(body: RequestBody, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    provider = RecordingProvider(build_provider(), db)
    result = classify_request(db, provider, body.text, body.source, request.state.request_id)
    return envelope(request, result)


@router.get("/requests")
def get_requests(request: Request, status: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    return envelope(request, {"requests": list_requests(db, status)})


@router.post("/requests/{request_id}/review")
def post_review(
    request_id: str,
    body: ReviewBody,
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_user),
):
    require_user(user, "reviewer")
    return envelope(request, review_request(db, request_id, user.id, body.decision, body.category))


@router.get("/requests/export")
def get_export(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    require_user(user, "reviewer")
    return envelope(request, {"rows": export_labels(db)})


@router.post("/agent")
def post_agent(body: AgentBody, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    runtime = get_runtime()
    reader = build_reader(db, request.state.request_id, runtime.store, runtime.circuit)
    provider = RecordingProvider(build_provider(), db)
    result = run_agent_turn(
        db,
        provider,
        build_embedder(),
        reader,
        user_id=user.id,
        message=body.message,
        conversation_id=body.conversation_id,
        request_id=request.state.request_id,
    )
    return envelope(request, result)


@router.get("/actions")
def get_actions(request: Request, status: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    return envelope(request, {"actions": list_actions(db, status)})


@router.get("/actions/{action_id}")
def get_action(action_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    from app.db.models import Action

    action = db.get(Action, action_id)
    if action is None:
        raise AppError("not_found", "Action not found.", 404)
    return envelope(request, {**action_payload(action), "events": action_events(db, action_id)})


@router.post("/actions/{action_id}/submit")
def post_submit(action_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    return envelope(request, submit_action(db, action_id, user.id))


@router.post("/actions/{action_id}/approve")
def post_approve(action_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    require_user(user, "approver")
    return envelope(request, approve_action(db, action_id, user.id))


@router.post("/actions/{action_id}/reject")
def post_reject(action_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    require_user(user, "approver")
    return envelope(request, reject_action(db, action_id, user.id))


@router.post("/actions/{action_id}/edit")
def post_edit(action_id: str, body: EditBody, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_user)):
    require_user(user, "approver")
    return envelope(request, edit_action(db, action_id, user.id, body.payload))


@router.post("/actions/{action_id}/execute")
async def post_execute(action_id: str, request: Request, db: Session = Depends(get_db)):
    actor = _actor_for_execute(request)
    result = execute_action(db, action_id, actor, request.state.request_id)
    return envelope(request, result)


@router.post("/actions/{action_id}/n8n-result")
async def post_n8n_result(action_id: str, request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    path = request.url.path
    result = accept_n8n_result(db, action_id, body, dict(request.headers), path)
    return envelope(request, result)


@router.get("/usage")
def get_usage(
    request: Request,
    from_: str | None = None,
    to: str | None = None,
    group_by: str = "task",
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_user),
):
    require_user(user, "approver")
    # Query name `from` is reserved in Python; accept both.
    start_raw = request.query_params.get("from") or from_
    report = usage_report(db, _parse_time(start_raw), _parse_time(to), group_by)
    return envelope(request, report)


@router.get("/usage/export")
def get_usage_export(
    request: Request,
    to: str | None = None,
    group_by: str = "task",
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_user),
):
    require_user(user, "approver")
    start_raw = request.query_params.get("from")
    report = usage_report(db, _parse_time(start_raw), _parse_time(to), group_by)
    from fastapi.responses import Response

    return Response(
        content=usage_csv(report),
        media_type="text/csv",
        headers={"X-Request-Id": request.state.request_id, "Content-Disposition": "attachment; filename=usage.csv"},
    )


@router.get("/me")
def get_me(request: Request, user: CurrentUser = Depends(get_user)):
    return envelope(request, {"id": user.id, "roles": sorted(user.roles)})


def _actor_for_execute(request: Request) -> str:
    settings = get_settings()
    timestamp = request.headers.get("x-timestamp")
    signature = request.headers.get("x-signature")
    if timestamp and signature:
        from app.webhooks.sign import verify

        path = request.url.path
        # Body may already have been consumed by FastAPI; execute is usually empty.
        verify(settings.n8n_signing_secret, timestamp, "POST", path, b"", signature)
        return "n8n"
    user = user_from_header(request.headers.get("authorization"))
    require_user(user, "approver")
    return user.id


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
