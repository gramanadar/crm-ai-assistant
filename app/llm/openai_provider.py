"""OpenAI adapter. Structured output uses the official parse helper and the shared schema."""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.llm.base import StructuredResult, ToolCall, ToolSpec, ToolTurn, Usage


class OpenAIProvider:
    name = "openai"

    def __init__(self, client=None):
        self._client = client

    def _chat(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI

        settings = get_settings()
        return OpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds, max_retries=2).chat.completions

    def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel], task: str, request_id: str) -> StructuredResult:
        settings = get_settings()
        model = settings.model_for_task(task)
        started = time.perf_counter()
        response = self._chat().parse(
            model=model,
            messages=_openai_messages(system, messages),
            response_format=schema,
            timeout=settings.llm_timeout_seconds,
        )
        usage = _usage(response.usage)
        latency = int((time.perf_counter() - started) * 1000)
        choice = response.choices[0]
        finish = choice.finish_reason or ""
        refusal = getattr(choice.message, "refusal", None)
        if finish == "length" or refusal:
            return StructuredResult(None, "", usage, "max_tokens" if finish == "length" else "refusal", self.name, model, latency, invalid=True)
        parsed = getattr(choice.message, "parsed", None)
        if parsed is None:
            return StructuredResult(None, "", usage, finish or "invalid", self.name, model, latency, invalid=True)
        try:
            if not isinstance(parsed, schema):
                parsed = schema.model_validate(parsed)
        except ValidationError:
            return StructuredResult(None, str(parsed), usage, "invalid", self.name, model, latency, invalid=True)
        return StructuredResult(parsed, parsed.model_dump_json(), usage, finish or "stop", self.name, model, latency)

    def run_tools(self, *, system: str, messages: list[dict], tools: list[ToolSpec], task: str, request_id: str) -> ToolTurn:
        settings = get_settings()
        model = settings.model_for_task(task)
        started = time.perf_counter()
        response = self._chat().create(
            model=model,
            messages=_openai_messages(system, messages),
            tools=[_openai_tool(tool) for tool in tools],
            tool_choice="auto",
            timeout=settings.llm_timeout_seconds,
        )
        usage = _usage(response.usage)
        latency = int((time.perf_counter() - started) * 1000)
        choice = response.choices[0]
        finish = choice.finish_reason or ""
        refusal = getattr(choice.message, "refusal", None)
        if finish == "length" or refusal:
            return ToolTurn(usage=usage, stop_reason="max_tokens" if finish == "length" else "refusal", provider=self.name, model=model, latency_ms=latency, invalid=True)
        calls = []
        for call in choice.message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return ToolTurn(
            tool_calls=calls,
            text=choice.message.content,
            usage=usage,
            stop_reason=finish or "stop",
            provider=self.name,
            model=model,
            latency_ms=latency,
        )


def _openai_messages(system: str, messages: list[dict]) -> list[dict]:
    native = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role")
        if role == "tool":
            native.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": message.get("content") or "",
                }
            )
        elif role == "assistant" and message.get("tool_calls"):
            native.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments") or {})},
                        }
                        for call in message["tool_calls"]
                    ],
                }
            )
        else:
            native.append({"role": "assistant" if role == "assistant" else "user", "content": message.get("content") or ""})
    return native


def _openai_tool(tool: ToolSpec) -> dict:
    parameters = dict(tool.parameters)
    parameters.setdefault("additionalProperties", False)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
            "strict": True,
        },
    }


def _usage(usage) -> Usage:
    if usage is None:
        return Usage()
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    return Usage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_tokens=cached,
        cache_write_tokens=0,
    )
