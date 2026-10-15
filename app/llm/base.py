"""Provider interface shared by Claude, OpenAI, and the offline fake."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class StructuredResult:
    parsed: BaseModel | None
    raw_text: str
    usage: Usage
    stop_reason: str
    provider: str
    model: str
    latency_ms: int
    invalid: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolTurn:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    parsed: BaseModel | None = None
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    invalid: bool = False


class LLMProvider(Protocol):
    name: str

    def structured(
        self,
        *,
        system: str,
        messages: list[dict],
        schema: type[BaseModel],
        task: str,
        request_id: str,
    ) -> StructuredResult: ...

    def run_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
        task: str,
        request_id: str,
    ) -> ToolTurn: ...
