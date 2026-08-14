"""R6 reliable lifecycle, transactional Outbox, leases, and fencing routes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0006"
down_revision: str | None = "20260814_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_versions DROP CONSTRAINT IF EXISTS "
        "uq_document_versions_compilation"
    )
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("purge_after", sa.DateTime(timezone=True)))
    op.add_column(
        "documents", sa.Column("revision_token", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("document_versions", sa.Column("superseded_at", sa.DateTime(timezone=True)))
    op.add_column("document_versions", sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.add_column("ingestion_jobs", sa.Column("operation_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "ingestion_jobs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column("ingestion_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("ingestion_jobs", sa.Column("lease_owner", sa.String(200)))
    op.add_column("ingestion_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ingestion_jobs",
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("ingestion_jobs", sa.Column("failure_class", sa.String(32)))

    op.add_column(
        "index_versions",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("index_versions", sa.Column("superseded_at", sa.DateTime(timezone=True)))
    op.add_column("index_versions", sa.Column("purge_after", sa.DateTime(timezone=True)))

    op.create_table(
        "ingestion_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task", sa.String(50), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "task", name="uq_ingestion_tasks_job_task"),
    )
    op.create_index("ix_ingestion_tasks_job", "ingestion_tasks", ["job_id", "status"])
    op.create_table(
        "index_routes",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("active_index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "lifecycle_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True)),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("purge_after", sa.DateTime(timezone=True)),
        sa.Column("failure_class", sa.String(32)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.String(1000)),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_lifecycle_tenant_key"),
    )
    op.create_index(
        "ix_lifecycle_operations_state",
        "lifecycle_operations",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_table(
        "outbox_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", sa.String(200), nullable=False, unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.String(200), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(200)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_outbox_dispatch", "outbox_messages", ["status", "available_at", "lease_expires_at"]
    )
    op.create_table(
        "lifecycle_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "operation_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_lifecycle_batch_tenant_key"
        ),
        sa.CheckConstraint(
            "concurrency >= 1 AND concurrency <= 100", name="ck_lifecycle_batch_concurrency"
        ),
    )


def downgrade() -> None:
    op.drop_table("lifecycle_batches")
    op.drop_index("ix_outbox_dispatch", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_lifecycle_operations_state", table_name="lifecycle_operations")
    op.drop_table("lifecycle_operations")
    op.drop_table("index_routes")
    op.drop_index("ix_ingestion_tasks_job", table_name="ingestion_tasks")
    op.drop_table("ingestion_tasks")
    for name in ("purge_after", "superseded_at", "fencing_token"):
        op.drop_column("index_versions", name)
    for name in (
        "failure_class",
        "cancellation_requested",
        "lease_expires_at",
        "lease_owner",
        "next_attempt_at",
        "max_attempts",
        "attempts",
        "operation_id",
    ):
        op.drop_column("ingestion_jobs", name)
    for name in ("deleted_at", "superseded_at"):
        op.drop_column("document_versions", name)
    # R3 represented one compilation tuple as one version. R6 permits immutable reparse
    # snapshots of the same source, so a downgrade must fold those snapshots back into the
    # earliest compatible version before restoring the R3 uniqueness constraint.
    op.execute(
        "CREATE TEMP TABLE r6_duplicate_versions ON COMMIT DROP AS "
        "SELECT id AS duplicate_id, canonical_id FROM ("
        "SELECT id, first_value(id) OVER ("
        "PARTITION BY tenant_id, document_id, source_sha256, chunk_method "
        "ORDER BY revision) AS canonical_id, row_number() OVER ("
        "PARTITION BY tenant_id, document_id, source_sha256, chunk_method "
        "ORDER BY revision) AS duplicate_rank FROM document_versions"
        ") ranked WHERE duplicate_rank > 1"
    )
    op.execute(
        "UPDATE document_versions canonical SET status='active', "
        "activated_at=duplicate.activated_at FROM r6_duplicate_versions folded "
        "JOIN document_versions duplicate ON duplicate.id=folded.duplicate_id "
        "WHERE canonical.id=folded.canonical_id AND duplicate.status='active'"
    )
    op.execute(
        "DELETE FROM upload_idempotency_keys WHERE job_id IN ("
        "SELECT id FROM ingestion_jobs WHERE document_version_id IN ("
        "SELECT duplicate_id FROM r6_duplicate_versions))"
    )
    op.execute(
        "DELETE FROM ingestion_jobs WHERE document_version_id IN ("
        "SELECT duplicate_id FROM r6_duplicate_versions)"
    )
    op.execute(
        "DELETE FROM document_blocks WHERE document_version_id IN ("
        "SELECT duplicate_id FROM r6_duplicate_versions)"
    )
    op.execute(
        "DELETE FROM document_chunks WHERE document_version_id IN ("
        "SELECT duplicate_id FROM r6_duplicate_versions)"
    )
    op.execute(
        "DELETE FROM document_versions WHERE id IN ("
        "SELECT duplicate_id FROM r6_duplicate_versions)"
    )
    op.execute(
        "ALTER TABLE document_versions DROP CONSTRAINT IF EXISTS "
        "uq_document_versions_compilation"
    )
    op.create_unique_constraint(
        "uq_document_versions_compilation",
        "document_versions",
        ["tenant_id", "document_id", "source_sha256", "chunk_method"],
    )
    for name in ("revision_token", "purge_after", "deleted_at"):
        op.drop_column("documents", name)
