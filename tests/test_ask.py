from app.config import get_settings
from app.db.models import Chunk, LLMCall
from app.llm.fake import script_responses
from tests.conftest import USER
from tests.helpers import docx_bytes, upload


def _policy(client):
    data = docx_bytes(
        [
            ("Heading 1", "Refunds"),
            ("Normal", "Customers may request a refund within 30 days under policy POL-REF-30."),
        ]
    )
    return upload(client, "refunds.docx", data)


def test_answered_question_includes_citation(client):
    _policy(client)
    response = client.post("/v1/ask", json={"question": "What is the refund policy POL-REF-30?"}, headers=USER)
    body = response.json()["data"]
    assert response.status_code == 200
    assert body["status"] == "answered"
    assert body["citations"]
    citation = body["citations"][0]
    assert citation["document_title"]
    assert citation["heading_path"] == "Refunds"
    assert "POL-REF-30" in citation["text"]
    assert body["claims"][0]["chunk_ids"]


def test_unsupported_question_is_not_found_without_inventing(client, db_session):
    _policy(client)
    response = client.post("/v1/ask", json={"question": "What is the CEO salary?"}, headers=USER)
    body = response.json()["data"]
    assert body["status"] == "not_found"
    assert body["answer"] == ""
    assert body["missing_information"]
    assert body["citations"] == []
    assert "salary" not in body["answer"]


def test_empty_retrieval_skips_the_model(client, db_session):
    _policy(client)
    get_settings().similarity_floor = 0.99
    response = client.post("/v1/ask", json={"question": "zzzzqqq nomatch token"}, headers=USER)
    assert response.json()["data"]["status"] == "not_found"
    assert db_session.query(LLMCall).count() == 0


def test_fabricated_citation_is_rejected_then_repaired(client, db_session):
    _policy(client)
    chunk_id = db_session.query(Chunk).one().id
    script_responses(
        [
            {"status": "answered", "answer": "Invented", "claims": [{"text": "Invented", "chunk_ids": ["missing"]}], "missing_information": ""},
            {"status": "answered", "answer": "30 days", "claims": [{"text": "Refunds take 30 days.", "chunk_ids": [chunk_id]}], "missing_information": ""},
        ]
    )
    response = client.post("/v1/ask", json={"question": "How long is the refund window?"}, headers=USER)
    body = response.json()["data"]
    assert body["status"] == "answered"
    assert body["citations"][0]["chunk_id"] == chunk_id
    assert body["citations"][0]["chunk_id"] != "missing"


def test_empty_claims_downgrade_to_not_found(client):
    _policy(client)
    script_responses(
        [
            {"status": "answered", "answer": "A bare answer", "claims": [], "missing_information": ""},
            {"status": "answered", "answer": "Still bare", "claims": [], "missing_information": ""},
        ]
    )
    response = client.post("/v1/ask", json={"question": "How long is the refund window?"}, headers=USER)
    body = response.json()["data"]
    assert body["status"] == "not_found"
    assert body["answer"] == ""


def test_partial_answer_keeps_claim_and_missing_note(client, db_session):
    _policy(client)
    chunk_id = db_session.query(Chunk).one().id
    script_responses(
        [
            {
                "status": "partial",
                "answer": "Refunds take 30 days.",
                "claims": [{"text": "Refunds take 30 days.", "chunk_ids": [chunk_id]}],
                "missing_information": "The documents do not list the CEO salary.",
            }
        ]
    )
    response = client.post("/v1/ask", json={"question": "What is the refund window and the CEO salary?"}, headers=USER)
    body = response.json()["data"]
    assert body["status"] == "partial"
    assert body["claims"]
    assert "salary" in body["missing_information"]


def test_invalid_output_retries_once(client, db_session):
    _policy(client)
    chunk_id = db_session.query(Chunk).one().id
    script_responses(
        [
            {"_invalid": True},
            {"status": "answered", "answer": "30 days", "claims": [{"text": "30 days", "chunk_ids": [chunk_id]}], "missing_information": ""},
        ]
    )
    response = client.post("/v1/ask", json={"question": "refund window"}, headers=USER)
    assert response.json()["data"]["status"] == "answered"
    assert db_session.query(LLMCall).count() == 2


def test_unauthenticated_ask_is_rejected(client):
    response = client.post("/v1/ask", json={"question": "hello"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
