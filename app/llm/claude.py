"""Anthropic adapter. Structured output uses messages.parse and the shared Pydantic schema."""

from __future__ import annotations

import time

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.llm.base import StructuredResult, ToolCall, ToolSpec, ToolTurn, Usage


class ClaudeProvider:
    name = "claude"

    def __init__(self, client=None):
        self._client = client

    def _messages(self):
        if self._client is not None:
            return self._client
        from anthropic import Anthropic

        settings = get_settings()
        return Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=2)

    def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel], task: str, request_id: str) -> StructuredResult:
        settings = get_settings()
        model = settings.model_for_task(task)
        started = time.perf_counter()
        response = self._messages().parse(
            model=model,
            max_tokens=2048,
            system=system,
            messages=_claude_messages(messages),
            output_format=schema,
            timeout=settings.llm_timeout_seconds,
        )
        usage = _usage(response.usage)
        latency = int((time.perf_counter() - started) * 1000)
        stop_reason = response.stop_reason or ""
        if stop_reason in {"refusal", "max_tokens"}:
            return StructuredResult(None, "", usage, stop_reason, self.name, model, latency, invalid=True)
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            return StructuredResult(None, "", usage, stop_reason or "invalid", self.name, model, latency, invalid=True)
        try:
            if not isinstance(parsed, schema):
                parsed = schema.model_validate(parsed)
        except ValidationError:
            return StructuredResult(None, str(parsed), usage, "invalid", self.name, model, latency, invalid=True)
        return StructuredResult(parsed, parsed.model_dump_json(), usage, stop_reason or "end_turn", self.name, model, latency)

    def run_tools(self, *, system: str, messages: list[dict], tools: list[ToolSpec], task: str, request_id: str) -> ToolTurn:
        settings = get_settings()
        model = settings.model_for_task(task)
        started = time.perf_counter()
        response = self._messages().create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=_claude_messages(messages),
            tools=[_claude_tool(tool) for tool in tools],
            tool_choice={"type": "auto"},
            timeout=settings.llm_timeout_seconds,
        )
        usage = _usage(response.usage)
        latency = int((time.perf_counter() - started) * 1000)
        stop_reason = response.stop_reason or ""
        if stop_reason in {"refusal", "max_tokens"}:
            return ToolTurn(usage=usage, stop_reason=stop_reason, provider=self.name, model=model, latency_ms=latency, invalid=True)
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in response.content or []:
            block_type = getattr(block, "type", "")
            if block_type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
            elif block_type == "text":
                text_parts.append(block.text or "")
        return ToolTurn(
            tool_calls=calls,
            text="\n".join(text_parts) or None,
            usage=usage,
            stop_reason=stop_reason or "end_turn",
            provider=self.name,
            model=model,
            latency_ms=latency,
        )


def _claude_messages(messages: list[dict]) -> list[dict]:
    native = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            native.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("tool_call_id"),
                            "content": message.get("content") or "",
                            "is_error": bool(message.get("is_error")),
                        }
                    ],
                }
            )
        elif role == "assistant" and message.get("tool_calls"):
            content = []
            if message.get("content"):
                content.append({"type": "text", "text": message["content"]})
            for call in message["tool_calls"]:
                content.append(
                    {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("arguments") or {}}
                )
            native.append({"role": "assistant", "content": content})
        else:
            native.append({"role": "assistant" if role == "assistant" else "user", "content": message.get("content") or ""})
    return native


def _claude_tool(tool: ToolSpec) -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
        "strict": True,
    }


def _usage(usage) -> Usage:
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )
