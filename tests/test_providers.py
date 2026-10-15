from types import SimpleNamespace

from pydantic import BaseModel

from app.llm.claude import ClaudeProvider
from app.llm.openai_provider import OpenAIProvider
from app.rag.answer import AnswerPayload


class _ClaudeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed = parsed
        self.stop_reason = stop_reason
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            parsed_output=self.parsed,
            usage=SimpleNamespace(input_tokens=11, output_tokens=7, cache_read_input_tokens=3, cache_creation_input_tokens=2),
            content=[],
        )

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=4, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0),
            content=[SimpleNamespace(type="tool_use", id="tool_1", name="crm_lookup", input={"email": "ada@example.com"})],
        )


class _OpenAICompletions:
    def __init__(self, parsed, finish="stop", refusal=None):
        self.parsed = parsed
        self.finish = finish
        self.refusal = refusal
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(parsed=self.parsed, refusal=self.refusal, content=None, tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=self.finish, message=message)],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, prompt_tokens_details=SimpleNamespace(cached_tokens=3)),
        )

    def create(self, **kwargs):
        self.kwargs = kwargs
        function = SimpleNamespace(name="crm_lookup", arguments='{"email":"ada@example.com","record_id":""}')
        tool_call = SimpleNamespace(id="tool_1", function=function)
        message = SimpleNamespace(parsed=None, refusal=None, content=None, tool_calls=[tool_call])
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="tool_calls", message=message)],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, prompt_tokens_details=None),
        )


def test_both_providers_accept_the_same_answer_schema():
    payload = AnswerPayload(
        status="answered",
        answer="30 days",
        claims=[{"text": "30 days", "chunk_ids": ["c1"]}],
        missing_information="",
    )
    claude_messages = _ClaudeMessages(payload)
    openai_completions = _OpenAICompletions(payload)
    claude = ClaudeProvider(client=claude_messages)
    openai = OpenAIProvider(client=openai_completions)
    claude_result = claude.structured(system="s", messages=[{"role": "user", "content": "q"}], schema=AnswerPayload, task="ask", request_id="r")
    openai_result = openai.structured(system="s", messages=[{"role": "user", "content": "q"}], schema=AnswerPayload, task="ask", request_id="r")
    assert claude_result.parsed.model_dump() == openai_result.parsed.model_dump()
    assert claude_result.usage.input_tokens == 11
    assert claude_result.usage.cache_read_tokens == 3
    assert claude_result.usage.cache_write_tokens == 2
    assert openai_result.usage.cache_read_tokens == 3
    assert claude_messages.kwargs["output_format"] is AnswerPayload
    assert openai_completions.kwargs["response_format"] is AnswerPayload


def test_refusal_and_truncation_are_failures_on_both_providers():
    payload = AnswerPayload(status="not_found", answer="", claims=[], missing_information="missing")
    for stop in ("refusal", "max_tokens"):
        result = ClaudeProvider(client=_ClaudeMessages(payload, stop_reason=stop)).structured(
            system="s", messages=[], schema=AnswerPayload, task="ask", request_id="r"
        )
        assert result.invalid
        assert result.parsed is None
        assert result.stop_reason == stop
    refused = OpenAIProvider(client=_OpenAICompletions(payload, refusal="no")).structured(
        system="s", messages=[], schema=AnswerPayload, task="ask", request_id="r"
    )
    truncated = OpenAIProvider(client=_OpenAICompletions(payload, finish="length")).structured(
        system="s", messages=[], schema=AnswerPayload, task="ask", request_id="r"
    )
    assert refused.stop_reason == "refusal" and refused.parsed is None
    assert truncated.stop_reason == "max_tokens" and truncated.parsed is None


def test_tool_turns_keep_strict_schemas():
    claude_client = _ClaudeMessages(None)
    openai_client = _OpenAICompletions(None)
    claude = ClaudeProvider(client=claude_client)
    openai = OpenAIProvider(client=openai_client)
    from app.agent.tools import TOOL_SPECS

    claude_turn = claude.run_tools(system="s", messages=[], tools=TOOL_SPECS, task="agent", request_id="r")
    openai_turn = openai.run_tools(system="s", messages=[], tools=TOOL_SPECS, task="agent", request_id="r")
    assert claude_turn.tool_calls[0].name == "crm_lookup"
    assert openai_turn.tool_calls[0].name == "crm_lookup"
    assert claude_client.kwargs["tool_choice"] == {"type": "auto"}
    assert claude_client.kwargs["tools"][0]["strict"] is True
    assert openai_client.kwargs["tools"][0]["function"]["strict"] is True
    names = {tool["name"] for tool in claude_client.kwargs["tools"]}
    assert "crm_write" not in names


def test_factory_selects_provider_from_configuration(monkeypatch):
    from app.config import get_settings
    from app.llm.factory import build_provider

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    get_settings.cache_clear()
    assert build_provider().name == "claude"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    get_settings.cache_clear()
    assert build_provider().name == "openai"
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    assert build_provider().name == "fake"
