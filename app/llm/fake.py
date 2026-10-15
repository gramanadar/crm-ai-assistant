"""Offline provider. Scripted results take priority, then a deterministic heuristic."""

from __future__ import annotations

import json
import re
import time
from email.utils import parseaddr

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.llm.base import StructuredResult, ToolCall, ToolSpec, ToolTurn, Usage

_SCRIPT: list = []


def script_responses(items: list) -> None:
    _SCRIPT.clear()
    _SCRIPT.extend(items)


def clear_script() -> None:
    _SCRIPT.clear()


class FakeProvider:
    name = "fake"

    def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel], task: str, request_id: str) -> StructuredResult:
        started = time.perf_counter()
        model = get_settings().model_for_task(task)
        if _SCRIPT:
            item = _SCRIPT.pop(0)
            return self._from_script(item, schema, model, started)
        if schema.__name__ == "AnswerPayload":
            payload = _answer_from_prompt(messages[-1]["content"])
        elif schema.__name__ == "ClassificationResult":
            payload = _classify_text(_latest_user_text(messages))
        elif schema.__name__ == "AgentAnswer":
            payload = _agent_answer(messages)
        else:
            payload = {}
        return self._parsed(payload, schema, model, started)

    def run_tools(self, *, system: str, messages: list[dict], tools: list[ToolSpec], task: str, request_id: str) -> ToolTurn:
        started = time.perf_counter()
        model = get_settings().model_for_task(task)
        if _SCRIPT:
            item = _SCRIPT.pop(0)
            return self._tool_from_script(item, model, started)
        return _heuristic_tool_turn(messages, model, started)

    def _from_script(self, item, schema: type[BaseModel], model: str, started: float) -> StructuredResult:
        usage = Usage(input_tokens=12, output_tokens=8, cache_read_tokens=3, cache_write_tokens=1)
        latency = int((time.perf_counter() - started) * 1000)
        if item is None or (isinstance(item, dict) and item.get("_invalid")):
            return StructuredResult(
                parsed=None,
                raw_text="invalid",
                usage=usage,
                stop_reason=item.get("stop_reason", "invalid") if isinstance(item, dict) else "invalid",
                provider=self.name,
                model=model,
                latency_ms=latency,
                invalid=True,
            )
        if isinstance(item, dict) and item.get("_stop"):
            return StructuredResult(
                parsed=None,
                raw_text="",
                usage=usage,
                stop_reason=item["_stop"],
                provider=self.name,
                model=model,
                latency_ms=latency,
                invalid=True,
            )
        try:
            parsed = schema.model_validate(item)
        except ValidationError:
            return StructuredResult(
                parsed=None,
                raw_text=json.dumps(item),
                usage=usage,
                stop_reason="invalid",
                provider=self.name,
                model=model,
                latency_ms=latency,
                invalid=True,
            )
        return StructuredResult(
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            usage=usage,
            stop_reason="end_turn",
            provider=self.name,
            model=model,
            latency_ms=latency,
        )

    def _parsed(self, payload: dict, schema: type[BaseModel], model: str, started: float) -> StructuredResult:
        usage = Usage(input_tokens=20, output_tokens=12, cache_read_tokens=2, cache_write_tokens=0)
        latency = int((time.perf_counter() - started) * 1000)
        try:
            parsed = schema.model_validate(payload)
        except ValidationError:
            return StructuredResult(
                parsed=None,
                raw_text=json.dumps(payload),
                usage=usage,
                stop_reason="invalid",
                provider=self.name,
                model=model,
                latency_ms=latency,
                invalid=True,
            )
        return StructuredResult(
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            usage=usage,
            stop_reason="end_turn",
            provider=self.name,
            model=model,
            latency_ms=latency,
        )

    def _tool_from_script(self, item, model: str, started: float) -> ToolTurn:
        usage = Usage(input_tokens=15, output_tokens=6)
        latency = int((time.perf_counter() - started) * 1000)
        if isinstance(item, dict) and item.get("_stop"):
            return ToolTurn(
                usage=usage,
                stop_reason=item["_stop"],
                provider=self.name,
                model=model,
                latency_ms=latency,
                invalid=True,
            )
        if isinstance(item, dict) and item.get("tool_calls"):
            calls = [
                ToolCall(id=call.get("id", f"call_{index}"), name=call["name"], arguments=call.get("arguments", {}))
                for index, call in enumerate(item["tool_calls"])
            ]
            return ToolTurn(
                tool_calls=calls,
                usage=usage,
                stop_reason="tool_use",
                provider=self.name,
                model=model,
                latency_ms=latency,
            )
        if isinstance(item, dict) and item.get("answer"):
            return ToolTurn(
                text=json.dumps(item["answer"]),
                usage=usage,
                stop_reason="end_turn",
                provider=self.name,
                model=model,
                latency_ms=latency,
            )
        return ToolTurn(
            text=json.dumps(item),
            usage=usage,
            stop_reason="end_turn",
            provider=self.name,
            model=model,
            latency_ms=latency,
        )


def _latest_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content") or ""
    return ""


def _answer_from_prompt(prompt: str) -> dict:
    question = ""
    if "Question:" in prompt:
        question = prompt.split("Chunks follow", 1)[0].replace("Question:", "", 1).strip()
    chunks = []
    for match in re.finditer(
        r"\[chunk_id: (?P<id>[^\]]+)\]\n.*?<untrusted_chunk>\n(?P<body>.*?)\n</untrusted_chunk>",
        prompt,
        re.S,
    ):
        chunks.append({"id": match.group("id"), "text": match.group("body")})
    return _grounded_answer(question, chunks)


def _grounded_answer(question: str, chunks: list[dict]) -> dict:
    from app.rag.retrieve import tokenize

    tokens = tokenize(question)
    if not tokens or not chunks:
        return _not_found_payload()
    scored = []
    for chunk in chunks:
        haystack = set(tokenize(chunk["text"]))
        overlap = [token for token in tokens if token in haystack]
        if overlap:
            scored.append((len(set(overlap)), chunk, overlap))
    if not scored:
        return _not_found_payload()
    scored.sort(key=lambda item: item[0], reverse=True)
    _best_count, best, best_overlap = scored[0]
    found = set(best_overlap)
    claims = [{"text": best["text"][:500], "chunk_ids": [best["id"]]}]
    distinctive = [token for token in found if any(character.isdigit() or character == "-" for character in token)]
    if len(found) < 2 and not distinctive:
        return _not_found_payload()
    missing = [token for token in tokens if token not in found and len(token) > 3]
    if " and " in question.lower() and missing:
        return {
            "status": "partial",
            "answer": best["text"][:500],
            "claims": claims[:1],
            "missing_information": "The documents do not mention: " + ", ".join(missing[:8]),
        }
    return {
        "status": "answered",
        "answer": best["text"][:500],
        "claims": claims[:1],
        "missing_information": "",
    }


def _not_found_payload() -> dict:
    return {
        "status": "not_found",
        "answer": "",
        "claims": [],
        "missing_information": "The documents do not cover this question.",
    }


def _content_tokens(text: str) -> list[str]:
    stop = {"what", "when", "where", "which", "how", "does", "do", "is", "are", "the", "a", "an", "of", "to", "and", "for", "in", "on", "our", "please"}
    return [token for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()) if token not in stop and len(token) > 2]


def _classify_text(prompt: str) -> dict:
    text = prompt
    if "<untrusted_request>" in prompt:
        text = prompt.split("<untrusted_request>", 1)[1].split("</untrusted_request>", 1)[0]
    lowered = text.lower()
    scores = {
        "billing": _hits(lowered, ["refund", "invoice", "charge", "charged", "payment", "billing", "receipt"]),
        "technical": _hits(lowered, ["error", "bug", "crash", "outage", "password", "api", "timeout"]),
        "account": _hits(lowered, ["account", "profile", "email address", "close my"]),
        "sales": _hits(lowered, ["quote", "pricing", "purchase", "upgrade", "demo"]),
    }
    if scores["billing"] and ("refund" in lowered or "invoice" in lowered or "charge" in lowered):
        scores["technical"] = max(0, scores["technical"] - 2)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_name, top_score = ranked[0]
    second_score = ranked[1][1]
    email = _email(text)
    order_id = _order_id(text)
    extracted = {}
    if email:
        extracted["customer_email"] = email
    if order_id:
        extracted["order_id"] = order_id
    if top_score <= 0:
        return {
            "category": "other",
            "confidence": 0.4,
            "is_ambiguous": True,
            "reason": "The request does not match a defined category.",
            "extracted": extracted or None,
        }
    ambiguous = second_score > 0 and top_score - second_score <= 0
    confidence = min(0.95, 0.55 + 0.1 * top_score)
    return {
        "category": top_name,
        "confidence": confidence,
        "is_ambiguous": ambiguous,
        "reason": f"The request matches {top_name} based on its content.",
        "extracted": extracted or None,
    }


def _hits(text: str, words: list[str]) -> int:
    return sum(text.count(word) for word in words)


def _email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, re.I)
    if not match:
        return None
    _name, addr = parseaddr(match.group(0))
    return addr or match.group(0)


def _order_id(text: str) -> str | None:
    match = re.search(r"\b(?:ORD-)?\d{5,}\b", text, re.I)
    return match.group(0) if match else None


def _agent_answer(messages: list[dict]) -> dict:
    tool_blobs = [message.get("content") or "" for message in messages if message.get("role") == "tool"]
    joined = "\n".join(tool_blobs)
    successes = [blob for blob in tool_blobs if '"ok": true' in blob or '"ok":true' in blob]
    errors = [blob for blob in tool_blobs if '"is_error": true' in blob or '"is_error":true' in blob]
    if errors and not successes:
        message = "CRM unavailable"
        if "No record found" in joined:
            message = "No record found for that email."
        elif "couldn't reach" in joined:
            message = "I couldn't reach the CRM, so I can't confirm this record's details."
        return {
            "status": "failed",
            "answer": message,
            "claims": [],
            "missing_information": "",
            "crm_facts_used": ["invented"],
        }
    claims = []
    for match in re.finditer(r'"chunk_id": "([^"]+)"', joined):
        claims.append({"text": "Supported by the retrieved passage.", "chunk_ids": [match.group(1)]})
        break
    fact_ids = re.findall(r'"id": "([^"]+)"', joined)
    name = ""
    name_match = re.search(r'"name": "([^"]+)"', joined)
    if name_match:
        name = name_match.group(1)
    return {
        "status": "answered",
        "answer": f"Lookup found {name}." if name else "Here is what the tools returned.",
        "claims": claims,
        "missing_information": "",
        "crm_facts_used": fact_ids[:1],
    }


def _heuristic_tool_turn(messages: list[dict], model: str, started: float) -> ToolTurn:
    usage = Usage(input_tokens=18, output_tokens=7)
    latency = int((time.perf_counter() - started) * 1000)
    last_user = _latest_user_text(messages)
    tool_messages = [message for message in messages if message.get("role") == "tool"]
    if not tool_messages:
        email = _email(last_user)
        record = re.search(r"\b(?:case|contact|record|id)\s+([A-Za-z0-9_\-]+)", last_user, re.I)
        calls = []
        if email or record:
            arguments = {}
            if email:
                arguments["email"] = email
            elif record:
                arguments["record_id"] = record.group(1)
            calls.append(ToolCall(id="call_lookup", name="crm_lookup", arguments=arguments))
        if re.search(r"policy|refund|document|handbook|shipping", last_user, re.I):
            calls.append(ToolCall(id="call_search", name="search_documents", arguments={"query": last_user}))
        if re.search(r"\b(draft|note|task)\b", last_user, re.I):
            calls.append(
                ToolCall(
                    id="call_draft",
                    name="draft_crm_action",
                    arguments={
                        "kind": "task" if re.search(r"\btask\b", last_user, re.I) else "note",
                        "record_id": record.group(1) if record else "",
                        "email": email or "",
                        "title": "Follow up",
                        "body": last_user[:500],
                    },
                )
            )
        if calls:
            return ToolTurn(
                tool_calls=calls,
                usage=usage,
                stop_reason="tool_use",
                provider="fake",
                model=model,
                latency_ms=latency,
            )
    answer = _agent_answer(messages)
    return ToolTurn(
        text=json.dumps(answer),
        usage=usage,
        stop_reason="end_turn",
        provider="fake",
        model=model,
        latency_ms=latency,
    )
