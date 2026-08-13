"""R1 tenant, knowledge identity, model registry, and audit foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "tenants",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
    )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id_id"),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])
    op.create_table(
        "documents",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("knowledge_base_id", uuid, nullable=False),
        sa.Column("external_key", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "external_key",
            name="uq_documents_tenant_kb_external_key",
        ),
    )
    op.create_index("ix_documents_tenant_kb", "documents", ["tenant_id", "knowledge_base_id"])
    op.create_table(
        "document_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("document_id", uuid, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "document_id", "revision", name="uq_document_versions_revision"
        ),
        sa.CheckConstraint("revision > 0", name="ck_document_versions_revision"),
    )
    op.create_table(
        "model_registrations",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("input_cost_per_million", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_cost_per_million", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('chat', 'embedding', 'reranker')", name="ck_models_kind"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("actor_id", uuid, nullable=False),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_audit_events_tenant_time", "audit_events", ["tenant_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_tenant_time", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("model_registrations")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_tenant_kb", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_knowledge_bases_tenant_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_table("tenants")
