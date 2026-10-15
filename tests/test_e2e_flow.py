"""Upload, ask, classify, draft, approve, and one sandbox CRM write."""

from app.runtime import get_runtime
from tests.conftest import APPROVER, USER
from tests.fakes.fake_crm import FakeCRM
from tests.helpers import docx_bytes, upload


def test_end_to_end_path(client):
    fake = FakeCRM()
    runtime = get_runtime()
    runtime.reader_transport = fake
    runtime.writer_transport = fake
    uploaded = upload(
        client,
        "handbook.docx",
        docx_bytes([("Heading 1", "Refunds"), ("Normal", "Customers may request a refund within 30 days under policy POL-REF-30.")]),
    )
    assert uploaded.status_code == 200
    answer = client.post("/v1/ask", json={"question": "What is the refund policy POL-REF-30?"}, headers=USER)
    assert answer.json()["data"]["status"] == "answered"
    classified = client.post(
        "/v1/requests",
        json={"text": "Please refund the duplicate invoice payment.", "source": "email"},
        headers=USER,
    )
    assert classified.json()["data"]["category"] == "billing"
    from app.llm.fake import script_responses

    script_responses(
        [
            {
                "tool_calls": [
                    {
                        "name": "draft_crm_action",
                        "arguments": {
                            "kind": "note",
                            "title": "Refund follow-up",
                            "body": "Customer asked about a duplicate invoice.",
                            "record_id": "",
                            "email": "ada@example.com",
                            "due_date": "",
                            "priority": "",
                        },
                    }
                ]
            },
            {"answer": {"status": "answered", "answer": "Draft ready.", "claims": [], "missing_information": "", "crm_facts_used": []}},
        ]
    )
    drafted = client.post("/v1/agent", json={"message": "Draft a note for ada@example.com"}, headers=USER)
    draft_id = drafted.json()["data"]["drafts"][0]["id"]
    assert client.post(f"/v1/actions/{draft_id}/submit", headers=USER).status_code == 200
    assert client.post(f"/v1/actions/{draft_id}/approve", headers=APPROVER).json()["data"]["status"] == "APPROVED"
    executed = client.post(f"/v1/actions/{draft_id}/execute", headers=APPROVER)
    assert executed.json()["data"]["status"] == "SUCCEEDED"
    assert len(fake.writes) == 1
