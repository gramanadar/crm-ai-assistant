import threading

from app.actions.machine import transition
from app.actions.schema import payload_hash
from app.agent.tools import tool_names
from app.config import get_settings
from app.db.models import Action, ActionEvent, ExternalCall
from app.llm.fake import script_responses
from app.runtime import get_runtime
from tests.conftest import APPROVER, REVIEWER, USER
from tests.fakes.fake_crm import FakeCRM
from tests.helpers import docx_bytes, upload


def _draft(client, email="ada@example.com"):
    script_responses(
        [
            {
                "tool_calls": [
                    {
                        "name": "draft_crm_action",
                        "arguments": {
                            "kind": "note",
                            "title": "Follow up",
                            "body": "Called the customer about onboarding.",
                            "record_id": "",
                            "email": email,
                            "due_date": "",
                            "priority": "",
                        },
                    }
                ]
            },
            {"answer": {"status": "answered", "answer": "Draft saved.", "claims": [], "missing_information": "", "crm_facts_used": []}},
        ]
    )
    return client.post("/v1/agent", json={"message": "Draft a note"}, headers=USER)


def test_registered_tools_do_not_include_a_write():
    assert tool_names() == ["crm_lookup", "search_documents", "draft_crm_action"]
    assert "crm_write" not in tool_names()


def test_draft_does_not_call_crm_write_and_submit_audits(client, db_session):
    fake = FakeCRM()
    runtime = get_runtime()
    runtime.reader_transport = fake
    runtime.writer_transport = fake
    response = _draft(client)
    assert response.status_code == 200, response.text
    draft = response.json()["data"]["drafts"][0]
    assert draft["status"] == "DRAFT"
    assert fake.write_calls == 0
    assert draft["idempotency_key"]
    pending = client.post(f"/v1/actions/{draft['id']}/submit", headers=USER)
    assert pending.status_code == 200
    assert pending.json()["data"]["status"] == "PENDING_APPROVAL"
    listed = client.get("/v1/actions?status=pending", headers=APPROVER).json()["data"]["actions"]
    assert listed[0]["id"] == draft["id"]
    assert listed[0]["target_label"] == "Ada Lovelace"
    events = db_session.query(ActionEvent).filter(ActionEvent.action_id == draft["id"]).all()
    assert any(event.to_status == "PENDING_APPROVAL" and event.actor == "user" for event in events)


def test_unconfirmed_target_cannot_be_submitted(client):
    fake = FakeCRM()
    fake.lookup_status = 404
    runtime = get_runtime()
    runtime.reader_transport = fake
    runtime.writer_transport = fake
    response = _draft(client, email="missing@example.com")
    draft = response.json()["data"]["drafts"][0]
    assert draft["lookup_confirmed"] is False
    submit = client.post(f"/v1/actions/{draft['id']}/submit", headers=USER)
    assert submit.status_code == 400
    assert "unconfirmed" in submit.json()["error"]["message"]
    assert client.get("/v1/actions?status=pending", headers=APPROVER).json()["data"]["actions"] == []


def test_bad_date_is_rejected(client):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    script_responses(
        [
            {
                "tool_calls": [
                    {
                        "name": "draft_crm_action",
                        "arguments": {
                            "kind": "task",
                            "title": "Call back",
                            "body": "Discuss the case.",
                            "record_id": "cnt_ada",
                            "email": "",
                            "due_date": "13/40/2026",
                            "priority": "high",
                        },
                    }
                ]
            },
            {"answer": {"status": "answered", "answer": "Could not draft.", "claims": [], "missing_information": "", "crm_facts_used": []}},
        ]
    )
    response = client.post("/v1/agent", json={"message": "Draft a task"}, headers=USER)
    activity = response.json()["data"]["tool_activity"]
    assert activity[0]["is_error"] is True
    assert db_count_writes(fake) == 0


def db_count_writes(fake: FakeCRM) -> int:
    return fake.write_calls


def test_approve_reject_edit_and_roles(client, db_session):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft_id = _draft(client).json()["data"]["drafts"][0]["id"]
    client.post(f"/v1/actions/{draft_id}/submit", headers=USER)
    forbidden = client.post(f"/v1/actions/{draft_id}/approve", headers=USER)
    assert forbidden.status_code == 403
    reviewer = client.post(f"/v1/actions/{draft_id}/approve", headers=REVIEWER)
    assert reviewer.status_code == 403
    approved = client.post(f"/v1/actions/{draft_id}/approve", headers=APPROVER)
    assert approved.status_code == 200
    body = approved.json()["data"]
    assert body["status"] == "APPROVED"
    assert body["approved_payload_hash"]
    edited = client.post(
        f"/v1/actions/{draft_id}/edit",
        json={"payload": {**body["payload"], "title": "Edited title"}},
        headers=APPROVER,
    )
    assert edited.json()["data"]["status"] == "PENDING_APPROVAL"
    assert edited.json()["data"]["approved_payload_hash"] is None
    client.post(f"/v1/actions/{draft_id}/approve", headers=APPROVER)
    rejected_setup = _draft(client).json()["data"]["drafts"][0]["id"]
    client.post(f"/v1/actions/{rejected_setup}/submit", headers=USER)
    rejected = client.post(f"/v1/actions/{rejected_setup}/reject", headers=APPROVER)
    assert rejected.json()["data"]["status"] == "REJECTED"
    assert fake.write_calls == 0


def test_execute_once_and_replay_returns_original(client):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    first = client.post(f"/v1/actions/{draft}/execute", headers=APPROVER)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "SUCCEEDED"
    result_id = first.json()["data"]["crm_result_id"]
    assert fake.write_calls == 1
    second = client.post(f"/v1/actions/{draft}/execute", headers=APPROVER)
    assert second.json()["data"]["crm_result_id"] == result_id
    assert fake.write_calls == 1


def test_payload_changed_after_approval_is_not_written(client, db_session):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    action = db_session.get(Action, draft)
    payload = dict(action.payload)
    payload["title"] = "Tampered"
    action.payload = payload
    db_session.commit()
    response = client.post(f"/v1/actions/{draft}/execute", headers=APPROVER)
    assert response.status_code == 409
    assert fake.write_calls == 0
    db_session.expire_all()
    assert db_session.get(Action, draft).status == "PENDING_APPROVAL"


def test_concurrent_execute_writes_once(client, db_session):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    barrier = threading.Barrier(2)
    results = []

    def run():
        barrier.wait()
        results.append(client.post(f"/v1/actions/{draft}/execute", headers=APPROVER).json()["data"]["status"])

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("SUCCEEDED") == 2
    assert fake.write_calls == 1
    assert len(fake.writes) == 1


def test_timeout_after_save_verifies_without_a_second_write(client):
    fake = FakeCRM()
    fake.write_mode = "timeout_after_save"
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    response = client.post(f"/v1/actions/{draft}/execute", headers=APPROVER)
    body = response.json()["data"]
    assert body["status"] == "SUCCEEDED"
    assert fake.write_calls == 1


def test_timeout_before_save_can_be_retried_with_the_same_key(client):
    fake = FakeCRM()
    fake.write_mode = "timeout_before_save"
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    first = client.post(f"/v1/actions/{draft}/execute", headers=APPROVER).json()["data"]
    assert first["status"] == "UNKNOWN"
    second = client.post(f"/v1/actions/{draft}/execute", headers=APPROVER).json()["data"]
    assert second["status"] == "SUCCEEDED"
    assert len(fake.writes) == 1


def test_action_events_are_append_only(client, db_session):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    get_runtime().writer_transport = fake
    draft = _ready(client)
    client.post(f"/v1/actions/{draft}/execute", headers=APPROVER)
    event = db_session.query(ActionEvent).first()
    event.actor = "mutated"
    try:
        db_session.commit()
        assert False, "update should fail"
    except RuntimeError:
        db_session.rollback()


def test_agent_lookup_and_document_search(client):
    upload(client, "policy.docx", docx_bytes([("Heading 1", "Refunds"), ("Normal", "Refunds are available for 30 days.")]))
    response = client.post(
        "/v1/agent",
        json={"message": "Look up ada@example.com and the refund policy."},
        headers=USER,
    )
    body = response.json()["data"]
    names = [item["name"] for item in body["tool_activity"]]
    assert "crm_lookup" in names
    assert "search_documents" in names
    assert "Ada Lovelace" in body["answer"]
    assert body["citations"]


def test_sixth_tool_call_stops(client):
    calls = [{"tool_calls": [{"name": "crm_lookup", "arguments": {"email": "ada@example.com", "record_id": ""}}]} for _ in range(6)]
    script_responses(calls)
    response = client.post("/v1/agent", json={"message": "keep looking"}, headers=USER)
    body = response.json()["data"]
    assert body["error_code"] == "tool_limit"
    assert len(body["tool_activity"]) == 5


def test_invalid_tool_arguments_do_not_reach_crm(client):
    fake = FakeCRM()
    get_runtime().reader_transport = fake
    script_responses(
        [
            {"tool_calls": [{"name": "crm_lookup", "arguments": {"email": "", "record_id": "", "extra": "no"}}]},
            {"answer": {"status": "failed", "answer": "bad args", "claims": [], "missing_information": "", "crm_facts_used": []}},
        ]
    )
    client.post("/v1/agent", json={"message": "lookup"}, headers=USER)
    assert fake.calls == []


def test_agent_discards_invented_crm_facts(client):
    fake = FakeCRM()
    fake.lookup_status = 404
    get_runtime().reader_transport = fake
    script_responses(
        [
            {"tool_calls": [{"name": "crm_lookup", "arguments": {"email": "missing@example.com", "record_id": ""}}]},
            {
                "answer": {
                    "status": "answered",
                    "answer": "The contact is Ada Lovelace and the plan is Enterprise.",
                    "claims": [],
                    "missing_information": "",
                    "crm_facts_used": ["cnt_ada"],
                }
            },
        ]
    )
    body = client.post("/v1/agent", json={"message": "Find missing@example.com"}, headers=USER).json()["data"]
    assert "Ada" not in body["answer"]
    assert "No record found" in body["answer"]
    assert body["tool_activity"][0]["is_error"] is True


def test_lookup_timeout_is_retried_and_logged(client, db_session):
    fake = FakeCRM()
    fake.lookup_timeouts = 2
    get_runtime().reader_transport = fake
    script_responses(
        [
            {"tool_calls": [{"name": "crm_lookup", "arguments": {"email": "ada@example.com", "record_id": ""}}]},
            {"answer": {"status": "answered", "answer": "Ada Lovelace is on the Enterprise plan.", "claims": [], "missing_information": "", "crm_facts_used": ["cnt_ada"]}},
        ]
    )
    body = client.post("/v1/agent", json={"message": "Find ada@example.com"}, headers=USER).json()["data"]
    assert "couldn't reach the CRM" in body["answer"]
    assert "Ada" not in body["answer"]
    attempts = db_session.query(ExternalCall).filter(ExternalCall.endpoint == "/records").count()
    assert attempts == 2


def test_repeated_500_opens_the_circuit(client):
    fake = FakeCRM()
    fake.lookup_status = 500
    runtime = get_runtime()
    runtime.reader_transport = fake
    runtime.circuit.threshold = 2
    script_responses(
        [
            {"tool_calls": [{"name": "crm_lookup", "arguments": {"email": "ada@example.com", "record_id": ""}}]},
            {"answer": {"status": "failed", "answer": "nope", "claims": [], "missing_information": "", "crm_facts_used": []}},
        ]
    )
    first = client.post("/v1/agent", json={"message": "Find ada@example.com"}, headers=USER).json()["data"]
    assert first["answer"] == "CRM unavailable" or "CRM unavailable" in first["answer"]
    calls_after_first = len(fake.calls)
    script_responses(
        [
            {"tool_calls": [{"name": "crm_lookup", "arguments": {"email": "ada@example.com", "record_id": ""}}]},
            {"answer": {"status": "failed", "answer": "nope", "claims": [], "missing_information": "", "crm_facts_used": []}},
        ]
    )
    second = client.post("/v1/agent", json={"message": "Find ada@example.com again"}, headers=USER).json()["data"]
    assert "CRM unavailable" in second["answer"]
    assert len(fake.calls) == calls_after_first


def test_write_credential_is_separate_from_the_agent_path(client):
    source = open("app/agent/loop.py").read() + open("app/agent/tools.py").read() + open("app/crm/client.py").read()
    assert "crm_write_token" not in open("app/agent/loop.py").read()
    assert "crm_write_token" not in open("app/agent/tools.py").read()
    assert "def build_reader" in source
    assert get_settings().crm_read_token != get_settings().crm_write_token


def _ready(client) -> str:
    draft = _draft(client).json()["data"]["drafts"][0]["id"]
    assert client.post(f"/v1/actions/{draft}/submit", headers=USER).status_code == 200
    approved = client.post(f"/v1/actions/{draft}/approve", headers=APPROVER)
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "APPROVED"
    return draft


def test_transition_table_blocks_illegal_move(db_session):
    from app.db.models import Action
    from uuid import uuid4

    action = Action(
        id=str(uuid4()),
        idempotency_key=str(uuid4()),
        type="note",
        payload={"kind": "note", "record_id": "x", "title": "t", "body": "b", "due_date": None, "priority": None},
        status="DRAFT",
    )
    db_session.add(action)
    db_session.commit()
    try:
        transition(db_session, action, "SUCCEEDED", "tester")
        assert False
    except Exception as exc:
        assert getattr(exc, "code", "") == "validation_failed"
