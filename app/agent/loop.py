"""Explicit tool loop. LangGraph is not on the default path."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.schema import AGENT_SYSTEM, AgentAnswer
from app.agent.tools import TOOL_SPECS, run_tool, tool_names
from app.config import get_settings
from app.db.models import Conversation, Message
from app.errors import AppError
from app.llm.base import LLMProvider
from app.rag.embed import Embedder

ASSISTANT_UNAVAILABLE = "The assistant is temporarily unavailable."


def run_agent_turn(
    session: Session,
    provider: LLMProvider,
    embedder: Embedder,
    reader,
    *,
    user_id: str,
    message: str,
    conversation_id: str | None,
    request_id: str,
) -> dict:
    text = message.strip()
    if not text:
        raise AppError("validation_failed", "A message is required.", 400)
    settings = get_settings()
    conversation = _conversation(session, conversation_id, user_id)
    _add_message(session, conversation.id, "user", text)
    messages = _history(session, conversation.id)
    started = time.monotonic()
    executed = 0
    activity: list[dict] = []
    successes: dict[str, dict] = {}
    failures: list[dict] = []
    retrieved: dict[str, dict] = {}
    drafts: list[dict] = []
    answer_attempts = 0

    while True:
        if time.monotonic() - started > settings.agent_time_budget_seconds:
            return _finish(conversation.id, ASSISTANT_UNAVAILABLE, "failed", "time_budget", activity, drafts, [])
        turn = provider.run_tools(
            system=AGENT_SYSTEM,
            messages=messages,
            tools=TOOL_SPECS,
            task="agent",
            request_id=request_id,
        )
        if turn.invalid or turn.stop_reason in {"refusal", "max_tokens"}:
            answer_attempts += 1
            if answer_attempts > 1:
                return _finish(conversation.id, ASSISTANT_UNAVAILABLE, "failed", turn.stop_reason or "invalid", activity, drafts, [])
            messages.append({"role": "user", "content": "The previous response was unusable. Continue or answer with the schema."})
            continue
        if turn.tool_calls:
            _add_message(
                session,
                conversation.id,
                "assistant",
                turn.text or "",
                tool_calls=[{"id": call.id, "name": call.name, "arguments": call.arguments} for call in turn.tool_calls],
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text or "",
                    "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in turn.tool_calls],
                }
            )
            for call in turn.tool_calls:
                if executed >= settings.agent_max_tool_calls:
                    return _finish(
                        conversation.id,
                        f"Tool call limit reached ({settings.agent_max_tool_calls}).",
                        "failed",
                        "tool_limit",
                        activity,
                        drafts,
                        [],
                    )
                if call.name not in tool_names():
                    result = {"is_error": True, "message": "Unknown tool", "summary": "Unknown tool"}
                else:
                    result = run_tool(
                        call.name,
                        call.arguments,
                        session=session,
                        reader=reader,
                        embedder=embedder,
                        actor=user_id,
                        conversation_id=conversation.id,
                    )
                executed += 1
                activity.append({"name": call.name, "summary": result.get("summary") or result.get("message"), "is_error": bool(result.get("is_error"))})
                if call.name == "crm_lookup" and result.get("ok") and result.get("id"):
                    successes[result["id"]] = result
                if call.name == "crm_lookup" and result.get("is_error"):
                    failures.append(result)
                if call.name == "search_documents":
                    for chunk in result.get("chunks") or []:
                        retrieved[chunk["chunk_id"]] = chunk
                if call.name == "draft_crm_action" and result.get("draft"):
                    drafts.append(result["draft"])
                content = json.dumps({key: value for key, value in result.items() if key != "draft"})
                _add_message(session, conversation.id, "tool", content, tool_name=call.name, tool_call_id=call.id, is_error=bool(result.get("is_error")))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content, "is_error": bool(result.get("is_error"))}
                )
            continue

        answer_attempts += 1
        parsed = _parse_answer(turn.text or "")
        if parsed is None:
            if answer_attempts > 1:
                return _finish(conversation.id, ASSISTANT_UNAVAILABLE, "failed", "invalid", activity, drafts, [])
            messages.append({"role": "user", "content": "Return a valid AgentAnswer JSON object."})
            continue
        finalized = _guard(parsed, successes, failures, retrieved)
        _add_message(session, conversation.id, "assistant", finalized["answer"])
        return _finish(
            conversation.id,
            finalized["answer"],
            finalized["status"],
            None,
            activity,
            drafts,
            finalized["citations"],
            missing=finalized["missing_information"],
            claims=finalized["claims"],
            crm_facts_used=finalized["crm_facts_used"],
        )


def _parse_answer(text: str) -> AgentAnswer | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1]
    try:
        return AgentAnswer.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError):
        return None


def _guard(answer: AgentAnswer, successes: dict, failures: list, retrieved: dict) -> dict:
    if failures and not successes:
        message = failures[-1].get("message") or "CRM unavailable"
        return {
            "status": "failed",
            "answer": message,
            "claims": [],
            "missing_information": "",
            "citations": [],
            "crm_facts_used": [],
        }
    if any(fact_id not in successes for fact_id in answer.crm_facts_used):
        return {
            "status": "failed",
            "answer": "I couldn't confirm this record's details.",
            "claims": [],
            "missing_information": "",
            "citations": [],
            "crm_facts_used": [],
        }
    claims = []
    for claim in answer.claims:
        ids = [chunk_id for chunk_id in claim.chunk_ids if chunk_id in retrieved]
        if len(ids) != len(claim.chunk_ids):
            return {
                "status": "not_found",
                "answer": "",
                "claims": [],
                "missing_information": "A document citation did not match a retrieved passage.",
                "citations": [],
                "crm_facts_used": list(answer.crm_facts_used),
            }
        if ids:
            claims.append({"text": claim.text, "chunk_ids": ids})
    citations = []
    seen = set()
    for claim in claims:
        for chunk_id in claim["chunk_ids"]:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunk = retrieved[chunk_id]
            citations.append(
                {
                    "chunk_id": chunk_id,
                    "document_title": chunk.get("document_title"),
                    "heading_path": chunk.get("heading_path"),
                    "page_from": chunk.get("page_from"),
                    "page_to": chunk.get("page_to"),
                    "text": chunk.get("text"),
                }
            )
    return {
        "status": answer.status if answer.status in {"answered", "partial", "not_found", "failed"} else "failed",
        "answer": answer.answer,
        "claims": claims,
        "missing_information": answer.missing_information,
        "citations": citations,
        "crm_facts_used": list(answer.crm_facts_used),
    }


def _finish(conversation_id, answer, status, error_code, activity, drafts, citations, missing="", claims=None, crm_facts_used=None) -> dict:
    return {
        "conversation_id": conversation_id,
        "status": status,
        "answer": answer,
        "error_code": error_code,
        "tool_activity": activity,
        "drafts": drafts,
        "citations": citations,
        "claims": claims or [],
        "missing_information": missing,
        "crm_facts_used": crm_facts_used or [],
    }


def _conversation(session: Session, conversation_id: str | None, user_id: str) -> Conversation:
    if conversation_id:
        found = session.get(Conversation, conversation_id)
        if found is None:
            raise AppError("not_found", "Conversation not found.", 404)
        return found
    conversation = Conversation(id=str(uuid4()), user_id=user_id)
    session.add(conversation)
    session.flush()
    return conversation


def _add_message(session, conversation_id, role, content, tool_name=None, tool_call_id=None, tool_calls=None, is_error=False) -> None:
    session.add(
        Message(
            id=str(uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content or "",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
            is_error=is_error,
        )
    )
    session.flush()


def _history(session: Session, conversation_id: str) -> list[dict]:
    rows = session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)).all()
    history = []
    for row in rows:
        item = {"role": row.role, "content": row.content}
        if row.tool_calls:
            item["tool_calls"] = row.tool_calls
        if row.tool_call_id:
            item["tool_call_id"] = row.tool_call_id
            item["name"] = row.tool_name
            item["is_error"] = row.is_error
        history.append(item)
    return history
