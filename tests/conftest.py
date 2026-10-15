import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import get_sessionmaker, reset_engine
from app.llm.fake import clear_script
from app.runtime import reset_runtime


@pytest.fixture()
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", "local-hash-v1")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "64")
    monkeypatch.setenv("N8N_INLINE", "false")
    monkeypatch.setenv("N8N_SIGNING_SECRET", "test-signing-secret")
    monkeypatch.setenv("CRM_READ_TOKEN", "read-token")
    monkeypatch.setenv("CRM_WRITE_TOKEN", "write-token")
    monkeypatch.setenv("DEBUG_LOG_BODIES", "false")
    get_settings.cache_clear()
    reset_engine()
    reset_runtime()
    clear_script()
    return url


@pytest.fixture()
def client(database_url: str) -> TestClient:
    from app.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    clear_script()
    reset_runtime()
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def db_session(client):
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
    finally:
        session.close()


USER = {"Authorization": "Bearer dev-user-token"}
REVIEWER = {"Authorization": "Bearer dev-reviewer-token"}
APPROVER = {"Authorization": "Bearer dev-approver-token"}
