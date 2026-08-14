"""R4 content-free retrieval trace detail and retention metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0004"
down_revision: str | None = "20260813_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retrieval_traces",
        sa.Column("canonical_query_sha256", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "retrieval_traces",
        sa.Column(
            "query_variant_sha256",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    for name in ("events", "candidate_traces", "fallback_steps"):
        op.add_column(
            "retrieval_traces",
            sa.Column(
                name,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="[]",
            ),
        )
    for name in ("filter_summary", "provider_ids"):
        op.add_column(
            "retrieval_traces",
            sa.Column(
                name,
                postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default="{}",
            ),
        )
    op.add_column(
        "retrieval_traces", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "retrieval_traces", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("retrieval_traces", sa.Column("error_code", sa.String(100), nullable=True))
    op.add_column("retrieval_traces", sa.Column("request_id", sa.String(200), nullable=True))
    op.add_column(
        "retrieval_traces",
        sa.Column(
            "index_version_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_index("ix_traces_expiry", "retrieval_traces", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_traces_expiry", table_name="retrieval_traces")
    for name in (
        "error_code",
        "index_version_ids",
        "request_id",
        "expires_at",
        "completed_at",
        "provider_ids",
        "filter_summary",
        "fallback_steps",
        "candidate_traces",
        "events",
        "query_variant_sha256",
        "canonical_query_sha256",
    ):
        op.drop_column("retrieval_traces", name)
