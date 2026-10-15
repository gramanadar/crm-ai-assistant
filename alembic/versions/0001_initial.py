"""Initial schema, plus pgvector and full-text columns on PostgreSQL."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="processing"),
        sa.Column("extraction_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("index_status", sa.String(32), nullable=False, server_default="not_started"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_sha256", "documents", ["sha256"], unique=True)

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embed_text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_embedding_model", "chunks", ["embedding_model"])

    op.create_table(
        "support_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(120), nullable=False, server_default="manual"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_ambiguous", sa.Boolean(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("extracted", sa.JSON(), nullable=True),
        sa.Column("model_output", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="review"),
        sa.Column("route_reason", sa.String(80), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(120), nullable=True),
        sa.Column("corrected_category", sa.String(80), nullable=True),
        sa.Column("final_category", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("split", sa.String(32), nullable=False, server_default="unlabeled"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_requests_status", "support_requests", ["status"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_name", sa.String(80), nullable=True),
        sa.Column("tool_call_id", sa.String(80), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("crm_record_id", sa.String(120), nullable=True),
        sa.Column("target_snapshot", sa.JSON(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("approved_payload_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("lookup_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crm_result_id", sa.String(120), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("n8n_result", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_actions_status", "actions", ["status"])
    op.create_index("uq_actions_idempotency_key", "actions", ["idempotency_key"], unique=True)

    op.create_table(
        "action_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("actions.id"), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("payload_snapshot", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_action_events_action_id", "action_events", ["action_id"])

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_reason", sa.String(64), nullable=False, server_default=""),
        sa.Column("est_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_calls_request_id", "llm_calls", ["request_id"])
    op.create_index("ix_llm_calls_task", "llm_calls", ["task"])
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])

    op.create_table(
        "external_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("endpoint", sa.String(300), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_class", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_external_calls_request_id", "external_calls", ["request_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding_vec vector_cosine_ops)"
        )
        op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector")
        op.execute(
            """
            CREATE OR REPLACE FUNCTION chunks_tsv_update() RETURNS trigger AS $$
            BEGIN
              NEW.tsv := to_tsvector('english', coalesce(NEW.embed_text, ''));
              RETURN NEW;
            END
            $$ LANGUAGE plpgsql
            """
        )
        op.execute("DROP TRIGGER IF EXISTS chunks_tsv_trigger ON chunks")
        op.execute(
            "CREATE TRIGGER chunks_tsv_trigger BEFORE INSERT OR UPDATE OF embed_text ON chunks "
            "FOR EACH ROW EXECUTE FUNCTION chunks_tsv_update()"
        )
        op.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS chunks_tsv_gin")
        op.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw")
    op.drop_table("webhook_events")
    op.drop_table("external_calls")
    op.drop_table("llm_calls")
    op.drop_table("action_events")
    op.drop_table("actions")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("support_requests")
    op.drop_table("chunks")
    op.drop_table("documents")
