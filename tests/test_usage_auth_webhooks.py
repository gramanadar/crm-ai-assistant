import json
from datetime import datetime, timezone

from app.config import get_settings
from app.db.models import LLMCall
from app.llm.usage import estimate_cost
from app.logging_config import redact
from app.webhooks.dispatch import accept_n8n_result, emit
from app.webhooks.sign import sign
from tests.conftest import APPROVER, USER
from tests.fakes.fake_crm import FakeCRM
from tests.helpers import docx_bytes, upload
from uuid import uuid4


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").status_code == 200


def test_ready_fails_when_database_is_down(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing' / 'db.sqlite'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings
    from app.db.session import reset_engine
    from app.main import create_app

    get_settings.cache_clear()
    reset_engine()
    application = create_app()
    from fastapi.testclient import TestClient

    with TestClient(application) as test_client:
        assert test_client.get("/health").status_code == 200
        ready = test_client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "not_ready"
    reset_engine()
    get_settings.cache_clear()


def test_usage_groups_and_export_match(client, db_session):
    upload(client, "policy.docx", docx_bytes([("Normal", "Refunds are available for 30 days under POL-REF-30.")]))
    client.post("/v1/ask", json={"question": "What is the refund policy POL-REF-30?"}, headers=USER)
    client.post("/v1/requests", json={"text": "Please refund this duplicate invoice payment."}, headers=USER)
    report = client.get("/v1/usage?group_by=task", headers=APPROVER)
    assert report.status_code == 200
    rows = {row["key"]: row for row in report.json()["data"]["rows"]}
    assert "ask" in rows and "classify" in rows
    exported = client.get("/v1/usage/export?group_by=task", headers=APPROVER)
    assert exported.status_code == 200
    for key, row in rows.items():
        assert key in exported.text
        assert str(row["input_tokens"]) in exported.text
    forbidden = client.get("/v1/usage", headers=USER)
    assert forbidden.status_code == 403


def test_price_change_does_not_rewrite_historical_cost(client, db_session):
    upload(client, "policy.docx", docx_bytes([("Normal", "Refunds are available for 30 days under POL-REF-30.")]))
    client.post("/v1/ask", json={"question": "What is the refund policy POL-REF-30?"}, headers=USER)
    first = db_session.query(LLMCall).one()
    original_cost = first.est_cost
    original_tokens = first.input_tokens
    table = get_settings().price_table
    table[first.model] = {"input": 100, "output": 100, "cache_read": 0, "cache_write": 0}
    get_settings().price_table_json = json.dumps(table)
    client.post("/v1/ask", json={"question": "What is the refund policy POL-REF-30?"}, headers=USER)
    db_session.expire_all()
    rows = db_session.query(LLMCall).order_by(LLMCall.created_at).all()
    assert rows[0].est_cost == original_cost
    assert rows[0].input_tokens == original_tokens
    assert rows[1].est_cost != original_cost


def test_budget_blocks_new_model_calls_but_not_execution(client, db_session):
    from app.runtime import get_runtime
    from tests.conftest import APPROVER as approver
    from tests.test_actions_and_agent import _ready

    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    db_session.add(
        LLMCall(
            id=str(uuid4()),
            request_id="req_seed",
            task="ask",
            provider="fake",
            model="local",
            input_tokens=50,
            output_tokens=10,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=1,
            stop_reason="end_turn",
            est_cost=0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    get_settings().daily_token_ceiling = 1
    blocked = client.post("/v1/requests", json={"text": "Please refund this duplicate invoice."}, headers=USER)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "budget_reached"
    executed = client.post(f"/v1/actions/{draft}/execute", headers=approver)
    assert executed.status_code == 200
    assert executed.json()["data"]["status"] == "SUCCEEDED"


def test_secrets_are_redacted():
    get_settings().anthropic_api_key = "sk-ant-secretvalue123"
    assert "sk-ant-secretvalue123" not in redact("key sk-ant-secretvalue123 leaked")
    assert "[REDACTED]" in redact("Bearer dev-user-token")


def test_webhook_signature_replay_and_inline_execute(client, db_session):
    from app.runtime import get_runtime
    from tests.test_actions_and_agent import _ready

    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    get_settings().n8n_inline = True
    payload = emit(db_session, "action.approved", {"action_id": draft, "idempotency_key": "x"})
    db_session.commit()
    assert fake.write_calls == 1
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = sign(get_settings().n8n_signing_secret, str(payload["timestamp"]), "POST", "/webhook/assistant-events", body)
    from app.webhooks.dispatch import apply_workflow

    second = apply_workflow(db_session, payload, body, signature, "/webhook/assistant-events")
    assert second["ignored"] is True
    assert fake.write_calls == 1

    event = {"event_id": "evt_callback_1", "status": "succeeded"}
    raw = json.dumps(event).encode()
    path = f"/v1/actions/{draft}/n8n-result"
    stamp = str(payload["timestamp"])
    good = sign(get_settings().n8n_signing_secret, stamp, "POST", path, raw)
    ok = client.post(path, content=raw, headers={"X-Signature": good, "X-Timestamp": stamp, "Content-Type": "application/json"})
    assert ok.status_code == 200
    assert ok.json()["data"]["stored"] is True
    replay = client.post(path, content=raw, headers={"X-Signature": good, "X-Timestamp": stamp, "Content-Type": "application/json"})
    assert replay.json()["data"]["ignored"] is True
    bad = client.post(path, content=raw, headers={"X-Signature": "sha256=deadbeef", "X-Timestamp": stamp, "Content-Type": "application/json"})
    assert bad.status_code == 401
    stale = sign(get_settings().n8n_signing_secret, "1", "POST", path, raw)
    old = client.post(path, content=raw, headers={"X-Signature": stale, "X-Timestamp": "1", "Content-Type": "application/json"})
    assert old.status_code == 401


def test_estimate_cost_uses_cache_tokens_separately():
    from app.llm.base import Usage

    get_settings().price_table_json = json.dumps({"m": {"input": 10, "output": 20, "cache_read": 1, "cache_write": 2}})
    cost = estimate_cost("m", Usage(input_tokens=1000, output_tokens=1000, cache_read_tokens=1000, cache_write_tokens=1000))
    assert cost == (10 + 20 + 1 + 2) / 1000
