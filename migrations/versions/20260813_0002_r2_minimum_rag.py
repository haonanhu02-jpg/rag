"""R2 minimum ingestion, pgvector index, and retrieval trace."""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0002"
down_revision: str | None = "20260813_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("knowledge_bases", sa.Column("owner_id", uuid, nullable=True))
    op.add_column(
        "knowledge_bases",
        sa.Column("description", sa.String(4000), nullable=False, server_default=""),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("visibility", sa.String(32), nullable=False, server_default="private"),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
    )
    op.add_column(
        "knowledge_bases", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE knowledge_bases SET owner_id = tenant_id, updated_at = created_at")
    op.alter_column("knowledge_bases", "owner_id", nullable=False)
    op.alter_column("knowledge_bases", "updated_at", nullable=False)
    op.create_check_constraint(
        "ck_knowledge_bases_visibility", "knowledge_bases", "visibility IN ('private', 'tenant')"
    )
    op.create_check_constraint(
        "ck_knowledge_bases_status", "knowledge_bases", "status IN ('active', 'deleted')"
    )

    for name, type_, default in (
        ("file_name", sa.String(500), "source"),
        ("media_type", sa.String(100), "text/plain"),
        ("object_key", sa.String(1000), "legacy/missing"),
    ):
        op.add_column(
            "document_versions", sa.Column(name, type_, nullable=False, server_default=default)
        )
    op.add_column(
        "document_versions",
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "document_versions", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        "uq_document_versions_source",
        "document_versions",
        ["tenant_id", "document_id", "source_sha256"],
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("knowledge_base_id", uuid, nullable=False),
        sa.Column("document_id", uuid, nullable=False),
        sa.Column("document_version_id", uuid, nullable=False),
        sa.Column("requested_by", uuid, nullable=False),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("trace_id", uuid, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_idempotency"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ingestion_jobs_status",
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name="ck_ingestion_jobs_progress"),
    )
    op.create_index("ix_jobs_pending", "ingestion_jobs", ["status", "created_at"])
    op.create_table(
        "upload_idempotency_keys",
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("job_id", uuid, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("tenant_id", "idempotency_key"),
    )

    op.create_table(
        "document_blocks",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("document_version_id", uuid, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_character", sa.Integer(), nullable=False),
        sa.Column("end_character", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_blocks_version_ordinal"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("knowledge_base_id", uuid, nullable=False),
        sa.Column("document_id", uuid, nullable=False),
        sa.Column("document_version_id", uuid, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_chunks_version_ordinal"),
    )
    op.create_index(
        "ix_chunks_authority",
        "document_chunks",
        ["tenant_id", "knowledge_base_id", "document_version_id"],
    )

    op.create_table(
        "index_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("knowledge_base_id", uuid, nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("embedding_model_id", sa.String(200), nullable=False),
        sa.Column("vector_dimensions", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "knowledge_base_id", "generation", name="uq_index_generation"
        ),
        sa.CheckConstraint("generation > 0", name="ck_index_generation"),
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'superseded', 'deleted')",
            name="ck_index_status",
        ),
    )
    op.create_index(
        "ix_index_active", "index_versions", ["tenant_id", "knowledge_base_id", "status"]
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("index_version_id", uuid, nullable=False),
        sa.Column("chunk_id", uuid, nullable=False),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("knowledge_base_id", uuid, nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.VECTOR(8), nullable=False),
        sa.ForeignKeyConstraint(["index_version_id"], ["index_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("index_version_id", "chunk_id"),
    )
    op.create_index(
        "ix_embeddings_scope",
        "chunk_embeddings",
        ["tenant_id", "knowledge_base_id", "index_version_id"],
    )

    op.create_table(
        "retrieval_traces",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("knowledge_base_ids", postgresql.ARRAY(uuid), nullable=False),
        sa.Column("query_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("selected_chunk_ids", postgresql.ARRAY(uuid), nullable=False),
        sa.Column("authorization_applied", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('success', 'no_evidence', 'failed')", name="ck_trace_status"
        ),
    )
    op.create_index("ix_traces_tenant_time", "retrieval_traces", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_traces_tenant_time", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")
    op.drop_index("ix_embeddings_scope", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index("ix_index_active", table_name="index_versions")
    op.drop_table("index_versions")
    op.drop_index("ix_chunks_authority", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_table("document_blocks")
    op.drop_table("upload_idempotency_keys")
    op.drop_index("ix_jobs_pending", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_constraint("uq_document_versions_source", "document_versions", type_="unique")
    for name in ("activated_at", "size_bytes", "object_key", "media_type", "file_name"):
        op.drop_column("document_versions", name)
    op.drop_constraint("ck_knowledge_bases_status", "knowledge_bases", type_="check")
    op.drop_constraint("ck_knowledge_bases_visibility", "knowledge_bases", type_="check")
    for name in ("updated_at", "status", "visibility", "description", "owner_id"):
        op.drop_column("knowledge_bases", name)
