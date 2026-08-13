"""R3 structured parser provenance and chunk-method selection."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0003"
down_revision: str | None = "20260813_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_document_versions_source", "document_versions", type_="unique")
    op.add_column(
        "document_versions",
        sa.Column("chunk_method", sa.String(32), nullable=False, server_default="general"),
    )
    op.create_check_constraint(
        "ck_document_versions_chunk_method",
        "document_versions",
        "chunk_method IN "
        "('general','paper','book','manual','laws','qa','table','resume','picture')",
    )
    op.create_unique_constraint(
        "uq_document_versions_compilation",
        "document_versions",
        ["tenant_id", "document_id", "source_sha256", "chunk_method"],
    )
    op.add_column(
        "document_versions", sa.Column("parser_name", sa.String(100), nullable=True)
    )
    op.add_column(
        "document_versions", sa.Column("parser_version", sa.String(50), nullable=True)
    )
    op.add_column(
        "document_versions", sa.Column("parse_schema_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "parse_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column("document_versions", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column(
        "document_versions", sa.Column("normalized_sha256", sa.String(64), nullable=True)
    )
    op.add_column("document_blocks", sa.Column("page_number", sa.Integer(), nullable=True))
    op.add_column(
        "document_blocks",
        sa.Column("bounding_box", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "document_blocks",
        sa.Column(
            "heading_path",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "document_blocks",
        sa.Column("table_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "document_blocks",
        sa.Column("media_reference", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("document_blocks", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "document_blocks",
        sa.Column("parser_name", sa.String(100), nullable=False, server_default="plain-text"),
    )
    op.add_column(
        "document_blocks",
        sa.Column("parser_version", sa.String(50), nullable=False, server_default="1"),
    )
    op.add_column(
        "document_blocks",
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_check_constraint(
        "ck_document_blocks_page", "document_blocks", "page_number IS NULL OR page_number >= 1"
    )
    op.create_check_constraint(
        "ck_document_blocks_confidence",
        "document_blocks",
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_document_blocks_confidence", "document_blocks", type_="check")
    op.drop_constraint("ck_document_blocks_page", "document_blocks", type_="check")
    for name in (
        "warnings",
        "parser_version",
        "parser_name",
        "confidence",
        "media_reference",
        "table_metadata",
        "heading_path",
        "bounding_box",
        "page_number",
    ):
        op.drop_column("document_blocks", name)
    op.drop_constraint("ck_document_versions_chunk_method", "document_versions", type_="check")
    for name in (
        "normalized_sha256",
        "page_count",
        "parse_warnings",
        "parse_schema_version",
        "parser_version",
        "parser_name",
    ):
        op.drop_column("document_versions", name)
    op.drop_constraint("uq_document_versions_compilation", "document_versions", type_="unique")
    op.drop_column("document_versions", "chunk_method")
    op.create_unique_constraint(
        "uq_document_versions_source",
        "document_versions",
        ["tenant_id", "document_id", "source_sha256"],
    )
