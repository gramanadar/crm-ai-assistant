from app.db.models import Chunk, Document
from app.rag.retrieve import VECTOR_SQL, FTS_SQL
from tests.conftest import APPROVER, USER
from tests.helpers import docx_bytes, pdf_bytes, upload


def test_pdf_and_docx_list_with_status(client, db_session):
    pdf = upload(client, "refund.pdf", pdf_bytes([[("Refund policy", 18), ("A refund is available for 30 days.", 11)]]))
    docx = upload(
        client,
        "shipping.docx",
        docx_bytes([("Heading 1", "Shipping"), ("Normal", "Standard shipping takes five business days.")]),
    )
    assert pdf.status_code == 200, pdf.text
    assert docx.status_code == 200, docx.text
    listed = client.get("/v1/documents", headers=USER)
    documents = listed.json()["data"]["documents"]
    assert len(documents) == 2
    assert {row["status"] for row in documents} <= {"ready", "ready_with_warnings"}
    assert all(row["uploaded_at"] and row["report"]["characters"] > 0 for row in documents)
    assert "request_id" in listed.json()


def test_same_bytes_are_a_noop(client, db_session):
    data = docx_bytes([("Normal", "The warehouse closes at 18:00.")])
    first = upload(client, "hours.docx", data).json()["data"]
    second = upload(client, "hours-copy.docx", data).json()["data"]
    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert db_session.query(Document).count() == 1
    assert db_session.query(Chunk).count() == first["chunk_count"]


def test_unsupported_type_is_rejected(client, db_session):
    rejected = upload(client, "notes.txt", b"hello")
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation_failed"
    image = upload(client, "scan.png", b"\x89PNG\r\n")
    assert image.status_code == 400
    assert image.json()["error"]["code"] == "validation_failed"
    assert db_session.query(Document).count() == 0


def test_library_cap(client, monkeypatch):
    from app.config import get_settings

    get_settings().max_documents = 2
    upload(client, "a.docx", docx_bytes([("Normal", "Alpha policy text for the library.")]))
    upload(client, "b.docx", docx_bytes([("Normal", "Beta policy text for the library.")]))
    third = upload(client, "c.docx", docx_bytes([("Normal", "Gamma policy text for the library.")]))
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "document_limit_reached"


def test_corrupt_file_is_listed_as_failed(client):
    response = upload(client, "broken.pdf", b"this is not a pdf")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "failed"
    assert body["extraction_status"] == "failed"
    assert body["index_status"] == "not_started"
    assert body["error_detail"]
    listed = client.get("/v1/documents", headers=APPROVER).json()["data"]["documents"]
    assert listed[0]["status"] == "failed"


def test_mixed_pdf_warns_and_still_indexes_text(client):
    data = pdf_bytes([[("Refund policy", 22), ("Customers may request a refund within 30 days.", 11)], []])
    body = upload(client, "mixed.pdf", data).json()["data"]
    assert body["status"] == "ready_with_warnings"
    assert any("Page 2" in warning for warning in body["warnings"])
    assert body["chunk_count"] >= 1
    assert body["report"]["pages"] == 2


def test_reindex_uses_new_chunk_settings(client, db_session):
    from app.config import get_settings

    text = "Renewal terms apply to every seat. " * 80
    created = upload(client, "long.docx", docx_bytes([("Heading 1", "Renewal"), ("Normal", text)])).json()["data"]
    assert created["chunk_count"] >= 1
    get_settings().chunk_target_min_tokens = 20
    get_settings().chunk_target_max_tokens = 40
    updated = client.post(f"/v1/documents/{created['id']}/reindex", headers=APPROVER)
    assert updated.status_code == 200
    assert updated.json()["data"]["chunk_count"] > created["chunk_count"]


def test_user_cannot_upload(client):
    response = upload(client, "a.docx", docx_bytes([("Normal", "Text")]), headers={"Authorization": "Bearer dev-user-token"})
    assert response.status_code == 403


def test_postgres_retrieval_sql_uses_pgvector_and_full_text():
    assert "<=>" in VECTOR_SQL
    assert "ts_rank" in FTS_SQL
    migration = open("alembic/versions/0001_initial.py").read()
    assert "hnsw" in migration
    assert "tsv" in migration
