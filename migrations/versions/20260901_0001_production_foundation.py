"""Production foundation: PostgreSQL, S3, outbox, audit and evals.

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260901_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

document_status = sa.Enum(
    "PENDING", "PROCESSING", "COMPLETED", "NEEDS_REVIEW", "FAILED", "PURGED",
    name="documentstatus", native_enum=False, length=32,
)
run_status = sa.Enum(
    "STARTED", "SUCCEEDED", "FAILED",
    name="runstatus", native_enum=False, length=20,
)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename_original", sa.String(255), nullable=False),
        sa.Column("filename_suggested", sa.String(255)),
        sa.Column("object_key", sa.String(1024)),
        sa.Column("file_hash", sa.String(64)),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_channel", sa.String(32), nullable=False),
        sa.Column("document_type", sa.String(100)),
        sa.Column("extracted_data", postgresql.JSONB()),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("status", document_status, nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("extractor_strategy", sa.String(80)),
        sa.Column("model_version", sa.String(120)),
        sa.Column("prompt_version", sa.String(120)),
        sa.Column("claimed_by", sa.String(255)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("last_modified_by", sa.String(255)),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_documents_file_hash_unique", "documents", ["file_hash"], unique=True)
    op.create_index("ix_documents_status_created", "documents", ["status", "created_at"])
    op.create_index(
        "ix_documents_review_claim", "documents",
        ["status", "claim_expires_at", "created_at"],
    )
    op.create_index(
        "ix_documents_retention", "documents",
        ["retention_until", "legal_hold", "status"],
    )

    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(120), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_extraction_runs_document_created", "extraction_runs",
        ["document_id", "created_at"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at", "created_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("actor_subject", sa.String(255), nullable=False),
        sa.Column("actor_roles", postgresql.JSONB(), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("changed_fields", postgresql.JSONB()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("source_ip_hash", sa.String(64)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_document_occurred", "audit_events", ["document_id", "occurred_at"])
    op.create_index("ix_audit_actor_occurred", "audit_events", ["actor_subject", "occurred_at"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_version", sa.String(120), nullable=False),
        sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(120), nullable=False),
        sa.Column("strategy", sa.String(80), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_eval_versions_created", "evaluation_runs",
        ["model_version", "prompt_version", "created_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_audit_event_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'audit_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER audit_events_immutable
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_event_mutation")
    op.drop_table("evaluation_runs")
    op.drop_table("audit_events")
    op.drop_table("outbox_events")
    op.drop_table("extraction_runs")
    op.drop_table("documents")
