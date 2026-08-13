from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from rag_platform.bootstrap.api import create_app
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import Settings
from rag_platform.bootstrap.worker import run as worker_run
from rag_platform.domain.identifiers import DocumentVersionId, JobId

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
ACTOR_A = "00000000-0000-0000-0000-000000000011"
ACTOR_B = "00000000-0000-0000-0000-000000000012"


@pytest.fixture
def r2_client(tmp_path: Path) -> Iterator[tuple[TestClient, R2Runtime]]:
    database_url = os.environ.get("RAG_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("RAG_TEST_DATABASE_URL is required for the real R2 vertical slice")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    settings = Settings("test", database_url, "INFO", str(tmp_path / "objects"), 4096)
    runtime = R2Runtime(settings)
    with TestClient(create_app(settings, runtime)) as client:
        yield client, runtime


def _headers(tenant: str, actor: str) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-actor-id": actor, "x-roles": "owner"}


def test_upload_ingest_query_citation_trace_and_idempotency(
    r2_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = r2_client
    headers = _headers(TENANT_A, ACTOR_A)
    created = client.post(
        "/v1/knowledge-bases",
        headers=headers,
        json={"name": "Maintenance", "visibility": "tenant"},
    )
    assert created.status_code == 201
    knowledge_base_id = created.json()["id"]
    upload_url = f"/v1/knowledge-bases/{knowledge_base_id}/documents"
    first = client.post(
        upload_url,
        headers={**headers, "Idempotency-Key": "upload-1"},
        files={
            "file": (
                "manual.md",
                "# Alarm Recovery\n\n故障复位前检查控制器和继电器。".encode(),
                "text/markdown",
            )
        },
    )
    duplicate = client.post(
        upload_url,
        headers={**headers, "Idempotency-Key": "upload-1"},
        files={
            "file": (
                "manual.md",
                "# Alarm Recovery\n\n故障复位前检查控制器和继电器。".encode(),
                "text/markdown",
            )
        },
    )
    same_content = client.post(
        upload_url,
        headers={**headers, "Idempotency-Key": "upload-2"},
        files={
            "file": (
                "manual.md",
                "# Alarm Recovery\n\n故障复位前检查控制器和继电器。".encode(),
                "text/markdown",
            )
        },
    )
    assert first.status_code == 202
    assert first.json()["status"] == "pending"
    assert duplicate.json()["job_id"] == first.json()["job_id"]
    assert duplicate.json()["duplicate"] is True
    assert same_content.json()["document_version_id"] == first.json()["document_version_id"]
    assert same_content.json()["duplicate"] is True

    assert worker_run(runtime) == 0
    completed = client.get(f"/v1/ingestion-jobs/{first.json()['job_id']}", headers=headers)
    assert completed.json()["progress"] == 1.0
    repeated = runtime.ingestion.run(JobId(UUID(first.json()["job_id"])))
    assert repeated.status.value == "succeeded"

    answer = client.post(
        "/v1/rag/query",
        headers=headers,
        json={
            "question": "故障复位应该检查什么?",
            "knowledge_base_ids": [knowledge_base_id],
            "top_k": 5,
            "top_n": 1,
        },
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == "answered"
    assert "[1]" in body["answer"]
    assert body["citations"][0]["document_version_id"] == first.json()["document_version_id"]
    assert body["citations"][0]["quote"]
    trace = client.get(f"/v1/retrieval-traces/{body['trace_id']}", headers=headers)
    assert trace.json()["authorization_applied"] is True
    assert trace.json()["candidate_count"] >= 1

    with runtime.engine.connect() as connection:
        counts = (
            connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM document_versions) AS versions, "
                    "(SELECT count(*) FROM document_chunks) AS chunks"
                )
            )
            .mappings()
            .one()
        )
    assert counts == {"versions": 1, "chunks": 1}


def test_hard_filters_hide_cross_tenant_wrong_kb_unpublished_and_deleted(
    r2_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = r2_client
    headers_a = _headers(TENANT_A, ACTOR_A)
    headers_b = _headers(TENANT_B, ACTOR_B)
    kb_a = client.post("/v1/knowledge-bases", headers=headers_a, json={"name": "A"}).json()["id"]
    kb_b = client.post("/v1/knowledge-bases", headers=headers_a, json={"name": "B"}).json()["id"]
    upload = client.post(
        f"/v1/knowledge-bases/{kb_a}/documents",
        headers={**headers_a, "Idempotency-Key": "scope-test"},
        files={"file": ("a.txt", b"authorized evidence", "text/plain")},
    ).json()
    pending_query = client.post(
        "/v1/rag/query",
        headers=headers_a,
        json={"question": "evidence", "knowledge_base_ids": [kb_a], "top_n": 1},
    )
    assert pending_query.json()["status"] == "no_evidence"
    assert runtime.models.chat_requests == []
    runtime.ingestion.run(runtime.repository.next_pending_job())  # type: ignore[arg-type]

    wrong_kb = client.post(
        "/v1/rag/query",
        headers=headers_a,
        json={"question": "evidence", "knowledge_base_ids": [kb_b], "top_n": 1},
    )
    cross_tenant = client.post(
        "/v1/rag/query",
        headers=headers_b,
        json={"question": "evidence", "knowledge_base_ids": [kb_a], "top_n": 1},
    )
    assert wrong_kb.json()["status"] == "no_evidence"
    assert cross_tenant.status_code == 404

    version_id = DocumentVersionId(UUID(upload["document_version_id"]))
    with runtime.engine.begin() as connection:
        connection.execute(
            text("UPDATE document_versions SET status='deleted' WHERE id=:id"),
            {"id": version_id.value},
        )
    deleted = client.post(
        "/v1/rag/query",
        headers=headers_a,
        json={"question": "evidence", "knowledge_base_ids": [kb_a], "top_n": 1},
    )
    assert deleted.json()["status"] == "no_evidence"


def test_upload_contract_rejects_missing_identity_media_and_idempotency_conflict(
    r2_client: tuple[TestClient, R2Runtime],
) -> None:
    client, _ = r2_client
    headers = _headers(TENANT_A, ACTOR_A)
    kb = client.post("/v1/knowledge-bases", headers=headers, json={"name": "KB"}).json()["id"]
    url = f"/v1/knowledge-bases/{kb}/documents"
    missing_identity = client.post(
        url,
        headers={"Idempotency-Key": "x"},
        files={"file": ("a.txt", b"a", "text/plain")},
    )
    unsupported = client.post(
        url,
        headers={**headers, "Idempotency-Key": "x"},
        files={"file": ("a.pdf", b"pdf", "application/pdf")},
    )
    client.post(
        url,
        headers={**headers, "Idempotency-Key": "first"},
        files={"file": ("a.txt", b"one", "text/plain")},
    )
    conflict = client.post(
        url,
        headers={**headers, "Idempotency-Key": "first"},
        files={"file": ("a.txt", b"two", "text/plain")},
    )
    assert missing_identity.status_code == 401
    assert unsupported.status_code == 415
    assert conflict.status_code == 409

    unsupported_filter = client.post(
        "/v1/rag/query",
        headers=headers,
        json={
            "question": "one",
            "knowledge_base_ids": [kb],
            "filters": [{"field": "document_id", "operator": "equals", "value": "x"}],
        },
    )
    assert unsupported_filter.status_code == 501
