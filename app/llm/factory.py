"""Select a provider from configuration and record every call."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm.base import LLMProvider, StructuredResult, ToolSpec, ToolTurn
from app.llm.claude import ClaudeProvider
from app.llm.fake import FakeProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.usage import enforce_budget, record_usage


def build_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "claude":
        return ClaudeProvider()
    if settings.llm_provider == "openai":
        return OpenAIProvider()
    return FakeProvider()


class RecordingProvider:
    """Records usage and applies the budget guard around a concrete provider."""

    def __init__(self, inner: LLMProvider, session: Session):
        self.inner = inner
        self.session = session
        self.name = inner.name

    def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel], task: str, request_id: str) -> StructuredResult:
        enforce_budget(self.session, task)
        result = self.inner.structured(system=system, messages=messages, schema=schema, task=task, request_id=request_id)
        record_usage(self.session, result, task, request_id)
        return result

    def run_tools(self, *, system: str, messages: list[dict], tools: list[ToolSpec], task: str, request_id: str) -> ToolTurn:
        enforce_budget(self.session, task)
        result = self.inner.run_tools(system=system, messages=messages, tools=tools, task=task, request_id=request_id)
        record_usage(self.session, result, task, request_id)
        return result
