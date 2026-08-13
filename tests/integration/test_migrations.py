from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_postgres_migration_round_trip() -> None:
    database_url = os.environ.get("RAG_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("RAG_TEST_DATABASE_URL is required for migration integration tests")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert {
            "tenants",
            "knowledge_bases",
            "documents",
            "document_versions",
            "model_registrations",
            "audit_events",
            "ingestion_jobs",
            "upload_idempotency_keys",
            "document_blocks",
            "document_chunks",
            "index_versions",
            "chunk_embeddings",
            "retrieval_traces",
        } <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
