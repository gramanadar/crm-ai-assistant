"""Runtime configuration. Business thresholds live here, not in feature code."""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "crm-ai-assistant"
    database_url: str = "sqlite:///./data/assistant.db"
    data_dir: str = "./data"

    llm_provider: str = "fake"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    voyage_api_key: str = ""

    model_ask: str = "claude-opus-5-5"
    model_classify: str = "claude-opus-5-5"
    model_agent: str = "claude-opus-5-5"
    openai_model_ask: str = "gpt-4.1"
    openai_model_classify: str = "gpt-4.1-mini"
    openai_model_agent: str = "gpt-4.1"

    embedding_provider: str = "local"
    embedding_model: str = "local-hash-v1"
    embedding_dimensions: int = 1536

    chunk_target_min_tokens: int = 500
    chunk_target_max_tokens: int = 800
    chunk_overlap_ratio: float = 0.12

    retrieval_top_k: int = 8
    similarity_floor: float = 0.2
    rrf_k: int = 60

    categories: str = "billing,technical,account,sales,other"
    classify_confidence_threshold: float = 0.75
    classify_double_check: bool = False

    max_documents: int = 10
    low_text_char_threshold: int = 20

    crm_base_url: str = "sandbox"
    crm_read_token: str = ""
    crm_write_token: str = ""
    crm_connect_timeout: float = 3.0
    crm_read_timeout: float = 10.0
    crm_circuit_threshold: int = 3
    crm_circuit_open_seconds: int = 60

    n8n_webhook_url: str = ""
    n8n_signing_secret: str = "dev-signing-secret"
    n8n_inline: bool = True
    webhook_max_age_seconds: int = 300
    public_app_url: str = "http://localhost:3000"

    daily_token_ceiling: int = 0
    price_table_json: str = (
        '{"claude-opus-5-5":{"input":15,"output":75,"cache_read":1.5,"cache_write":18.75},'
        '"gpt-4.1":{"input":2,"output":8,"cache_read":0.5,"cache_write":0},'
        '"gpt-4.1-mini":{"input":0.4,"output":1.6,"cache_read":0.1,"cache_write":0},'
        '"text-embedding-3-small":{"input":0.02,"output":0,"cache_read":0,"cache_write":0},'
        '"local-hash-v1":{"input":0,"output":0,"cache_read":0,"cache_write":0}}'
    )

    llm_timeout_seconds: float = 60.0
    agent_max_tool_calls: int = 5
    agent_time_budget_seconds: float = 30.0

    debug_log_bodies: bool = False
    log_level: str = "INFO"

    auth_tokens: str = "user:dev-user-token,reviewer:dev-reviewer-token,approver:dev-approver-token"
    category_channels_json: str = (
        '{"billing":"billing@example.com","technical":"technical@example.com",'
        '"account":"account@example.com","sales":"sales@example.com",'
        '"other":"support@example.com","review":"review@example.com"}'
    )

    @property
    def category_list(self) -> list[str]:
        return [part.strip() for part in self.categories.split(",") if part.strip()]

    @property
    def price_table(self) -> dict:
        return json.loads(self.price_table_json)

    @property
    def category_channels(self) -> dict[str, str]:
        return json.loads(self.category_channels_json)

    def model_for_task(self, task: str) -> str:
        if self.llm_provider == "openai":
            return {
                "ask": self.openai_model_ask,
                "classify": self.openai_model_classify,
                "agent": self.openai_model_agent,
            }.get(task, self.openai_model_ask)
        return {
            "ask": self.model_ask,
            "classify": self.model_classify,
            "agent": self.model_agent,
        }.get(task, self.model_ask)

    def secret_values(self) -> list[str]:
        values = [
            self.anthropic_api_key,
            self.openai_api_key,
            self.voyage_api_key,
            self.crm_read_token,
            self.crm_write_token,
            self.n8n_signing_secret,
        ]
        for token in self.auth_tokens.split(","):
            if ":" in token:
                values.append(token.split(":", 1)[1])
        return [value for value in values if value and len(value) >= 4]


@lru_cache
def get_settings() -> Settings:
    return Settings()
