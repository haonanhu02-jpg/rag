from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from rag_platform.bootstrap.api import create_app
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import Settings
from rag_platform.domain.identifiers import DocumentId, TenantId
from rag_platform.modules.lifecycle.contracts import (
    FailureClass,
    FailureDecision,
    LifecycleConflict,
)

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000011"


@pytest.fixture
def lifecycle_client(tmp_path: Path) -> Iterator[tuple[TestClient, R2Runtime]]:
    database_url = os.environ.get("RAG_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("RAG_TEST_DATABASE_URL is required for lifecycle E2E")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    settings = Settings(
        "test",
        database_url,
        "INFO",
        str(tmp_path / "objects"),
        4096,
        os.environ.get("RAG_TEST_ELASTICSEARCH_URL", "http://localhost:9200"),
        f"rag-r6-test-{tmp_path.name}".lower(),
    )
    runtime = R2Runtime(settings)
    with TestClient(create_app(settings, runtime)) as client:
        yield client, runtime


def _headers(key: str | None = None) -> dict[str, str]:
    values = {"x-tenant-id": TENANT, "x-actor-id": ACTOR, "x-roles": "owner"}
    if key is not None:
        values["Idempotency-Key"] = key
    return values


def _create_and_ingest(client: TestClient, runtime: R2Runtime) -> tuple[str, str, str]:
    kb = client.post(
        "/v1/knowledge-bases", headers=_headers(), json={"name": "Lifecycle"}
    ).json()["id"]
    upload = client.post(
        f"/v1/knowledge-bases/{kb}/documents",
        headers=_headers("initial"),
        files={"file": ("manual.txt", b"original safety relay", "text/plain")},
    ).json()
    assert runtime.lifecycle_worker.run_once() is True
    job = client.get(f"/v1/ingestion-jobs/{upload['job_id']}", headers=_headers()).json()
    assert job["status"] == "succeeded"
    assert job["operation_id"] is not None
    return kb, upload["document_id"], upload["document_version_id"]


def _query(client: TestClient, kb: str, question: str) -> dict[str, object]:
    value = client.post(
        "/v1/rag/query",
        headers=_headers(),
        json={"question": question, "knowledge_base_ids": [kb], "top_n": 2},
    ).json()
    return cast(dict[str, object], value)


def test_update_rollback_delete_restore_rebuild_and_batch(
    lifecycle_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = lifecycle_client
    kb, document_id, original_version = _create_and_ingest(client, runtime)

    reparsed = client.post(
        f"/v1/documents/{document_id}/reparse",
        headers=_headers("reparse-1"),
        json={"reason": "parser upgrade"},
    )
    assert reparsed.status_code == 202
    assert reparsed.json()["document_version_id"] != original_version
    assert runtime.lifecycle_worker.run_once() is True

    update = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("update-1"), "X-Lifecycle-Reason": "new manual"},
        files={"file": ("manual.txt", b"updated controller fuse", "text/plain")},
    )
    assert update.status_code == 202
    assert update.json()["status"] == "pending"
    duplicate_update = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("update-1"), "X-Lifecycle-Reason": "new manual"},
        files={"file": ("manual.txt", b"updated controller fuse", "text/plain")},
    )
    assert duplicate_update.status_code == 202
    assert duplicate_update.json()["id"] == update.json()["id"]
    conflicting_update = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("update-1"), "X-Lifecycle-Reason": "different audit reason"},
        files={"file": ("manual.txt", b"updated controller fuse", "text/plain")},
    )
    assert conflicting_update.status_code == 409
    assert _query(client, kb, "safety relay")["status"] == "answered"
    assert runtime.lifecycle_worker.run_once() is True
    updated_operation = client.get(
        f"/v1/lifecycle-operations/{update.json()['id']}", headers=_headers()
    ).json()
    assert updated_operation["status"] == "succeeded"
    updated_version = updated_operation["document_version_id"]
    assert updated_version != original_version
    assert _query(client, kb, "controller fuse")["status"] == "answered"

    rollback = client.post(
        f"/v1/documents/{document_id}/rollback",
        headers=_headers("rollback-1"),
        json={"reason": "bad update", "target_version_id": original_version},
    )
    assert rollback.json()["status"] == "pending"
    assert runtime.lifecycle_worker.run_once() is True
    assert _query(client, kb, "safety relay")["status"] == "answered"

    deleted = client.request(
        "DELETE",
        f"/v1/documents/{document_id}",
        headers=_headers("delete-1"),
        json={"reason": "retire manual"},
    )
    assert deleted.json()["status"] == "pending"
    assert _query(client, kb, "safety relay")["status"] == "no_evidence"
    assert runtime.lifecycle_worker.run_once() is True

    restored = client.post(
        f"/v1/documents/{document_id}/restore",
        headers=_headers("restore-1"),
        json={"reason": "deletion was accidental"},
    )
    assert restored.json()["status"] == "pending"
    assert _query(client, kb, "safety relay")["status"] == "no_evidence"
    assert runtime.lifecycle_worker.run_once() is True
    assert _query(client, kb, "safety relay")["status"] == "answered"

    with runtime.engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT fencing_token, active_index_version_id FROM index_routes "
                "WHERE knowledge_base_id=:kb"
            ),
            {"kb": kb},
        ).mappings().one()
    rebuild = client.post(
        f"/v1/knowledge-bases/{kb}/rebuild",
        headers=_headers("rebuild-1"),
        json={"reason": "mapping refresh"},
    ).json()
    assert runtime.lifecycle_worker.run_once() is True
    with runtime.engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT fencing_token, active_index_version_id FROM index_routes "
                "WHERE knowledge_base_id=:kb"
            ),
            {"kb": kb},
        ).mappings().one()
        active_count = connection.scalar(
            text(
                "SELECT count(*) FROM index_versions "
                "WHERE knowledge_base_id=:kb AND status='active'"
            ),
            {"kb": kb},
        )
    assert after["fencing_token"] == before["fencing_token"] + 1
    assert after["active_index_version_id"] != before["active_index_version_id"]
    assert active_count == 1

    rebuild_2 = client.post(
        f"/v1/knowledge-bases/{kb}/rebuild",
        headers=_headers("rebuild-2"),
        json={"reason": "second mapping refresh"},
    ).json()
    assert runtime.lifecycle_worker.run_once() is True

    batch = client.post(
        "/v1/lifecycle-batches",
        headers=_headers("batch-1"),
        json={
            "knowledge_base_id": kb,
            "kind": "rebuild",
            "operation_ids": [rebuild["id"], rebuild_2["id"]],
            "concurrency": 99,
        },
    )
    assert batch.status_code == 202
    assert batch.json()["status"] == "succeeded"
    assert batch.json()["succeeded"] == 2
    assert batch.json()["concurrency"] == 3

    wrong_kind = client.post(
        "/v1/lifecycle-batches",
        headers=_headers("batch-wrong-kind"),
        json={
            "knowledge_base_id": kb,
            "kind": "update",
            "operation_ids": [rebuild["id"]],
        },
    )
    assert wrong_kind.status_code == 409

    changed_duplicate = client.post(
        "/v1/lifecycle-batches",
        headers=_headers("batch-1"),
        json={
            "knowledge_base_id": kb,
            "kind": "rebuild",
            "operation_ids": [rebuild["id"], rebuild_2["id"]],
            "concurrency": 1,
        },
    )
    assert changed_duplicate.status_code == 409

    other_actor_headers = {**_headers(), "x-actor-id": "00000000-0000-0000-0000-000000000012"}
    assert client.get(
        f"/v1/lifecycle-batches/{batch.json()['id']}", headers=other_actor_headers
    ).status_code == 403


def test_cancel_and_permanent_failure_keep_old_active_version(
    lifecycle_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = lifecycle_client
    kb, document_id, original_version = _create_and_ingest(client, runtime)
    cancelled = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("cancel-update"), "X-Lifecycle-Reason": "will cancel"},
        files={"file": ("manual.txt", b"cancelled replacement", "text/plain")},
    ).json()
    result = client.post(
        f"/v1/lifecycle-operations/{cancelled['id']}/cancel", headers=_headers()
    ).json()
    assert result["status"] == "cancelled"
    assert runtime.lifecycle_worker.run_once() is False

    failed = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("failed-update"), "X-Lifecycle-Reason": "fault injection"},
        files={"file": ("manual.txt", b"missing replacement", "text/plain")},
    ).json()
    with runtime.engine.connect() as connection:
        object_key = connection.scalar(
            text("SELECT object_key FROM document_versions WHERE id=:id"),
            {"id": failed["document_version_id"]},
        )
    runtime.object_store.delete(tenant_id=TenantId(UUID(TENANT)), key=str(object_key))
    assert runtime.lifecycle_worker.run_once() is True
    failed_state = client.get(
        f"/v1/lifecycle-operations/{failed['id']}", headers=_headers()
    ).json()
    assert failed_state["status"] == "failed"
    assert failed_state["failure_class"] == "permanent"
    with runtime.engine.connect() as connection:
        active = connection.scalar(
            text(
                "SELECT id FROM document_versions "
                "WHERE document_id=:document AND status='active'"
            ),
            {"document": document_id},
        )
    assert str(active) == original_version
    assert _query(client, kb, "safety relay")["status"] == "answered"


def test_reconciliation_is_dry_run_first_and_schedules_safe_purge(
    lifecycle_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = lifecycle_client
    _, document_id, _ = _create_and_ingest(client, runtime)
    deleted = client.request(
        "DELETE",
        f"/v1/documents/{document_id}",
        headers=_headers("delete-purge"),
        json={"reason": "retention test"},
    ).json()
    assert runtime.lifecycle_worker.run_once() is True
    past = datetime.now(UTC) - timedelta(minutes=1)
    with runtime.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET purge_after=:past WHERE id=:id"),
            {"past": past, "id": document_id},
        )
    dry = runtime.lifecycle_reconciler.run()
    assert any(item.kind == "expired_document_tombstone" for item in dry.findings)
    with runtime.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_operations")) == 2
    applied = runtime.lifecycle_reconciler.run(dry_run=False)
    assert any(item.repaired for item in applied.findings)
    assert runtime.lifecycle_worker.run_once() is True
    with runtime.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM documents WHERE id=:id"), {"id": document_id}
        ) == 0
    purge_operation = client.get(
        f"/v1/lifecycle-operations/{deleted['id']}", headers=_headers()
    )
    assert purge_operation.status_code == 200


def test_reconciliation_detects_and_repairs_orphan_object_and_projection(
    lifecycle_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = lifecycle_client
    _create_and_ingest(client, runtime)
    tenant_id = TenantId(UUID(TENANT))
    orphan_key = "orphan/unreferenced.bin"
    runtime.object_store.put(tenant_id=tenant_id, key=orphan_key, value=b"orphan")

    projected = runtime.search._client.search(index=runtime.search._index, size=1)
    source = dict(projected["hits"]["hits"][0]["_source"])
    orphan_version = UUID(int=999)
    orphan_chunk = UUID(int=1000)
    source["document_version_id"] = str(orphan_version)
    source["chunk_id"] = str(orphan_chunk)
    runtime.search._client.index(
        index=runtime.search._index,
        id=str(orphan_chunk),
        document=source,
        refresh="wait_for",
    )

    dry = runtime.lifecycle_reconciler.run()
    dry_kinds = {item.kind for item in dry.findings}
    assert {"orphan_object", "orphan_projection"} <= dry_kinds
    assert runtime.object_store.get(tenant_id=tenant_id, key=orphan_key) == b"orphan"

    applied = runtime.lifecycle_reconciler.run(dry_run=False)
    assert {item.kind for item in applied.findings if item.repaired} >= {
        "orphan_object",
        "orphan_projection",
    }
    assert runtime.object_store.get(tenant_id=tenant_id, key=orphan_key) is None
    assert all(
        str(item.document_version_id) != str(orphan_version)
        for item in runtime.search.list_projection_versions()
    )


def test_reconciliation_reports_stale_operation_and_missing_outbox(
    lifecycle_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = lifecycle_client
    _, document_id, _ = _create_and_ingest(client, runtime)
    submitted = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("stale-update"), "X-Lifecycle-Reason": "stale operation"},
        files={"file": ("manual.txt", b"stale update source", "text/plain")},
    ).json()
    stale_at = datetime.now(UTC) - timedelta(minutes=20)
    with runtime.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM outbox_messages WHERE operation_id=:operation"),
            {"operation": submitted["id"]},
        )
        connection.execute(
            text("UPDATE lifecycle_operations SET updated_at=:at WHERE id=:operation"),
            {"at": stale_at, "operation": submitted["id"]},
        )

    report = runtime.lifecycle_reconciler.run()
    kinds = {item.kind for item in report.findings}
    assert {"stale_lifecycle_operation", "missing_outbox"} <= kinds


def test_worker_kill_duplicate_delivery_and_bounded_transient_retry(
    lifecycle_client: tuple[TestClient, R2Runtime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, runtime = lifecycle_client
    kb, document_id, _ = _create_and_ingest(client, runtime)
    update_response = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("lease-update"), "X-Lifecycle-Reason": "lease recovery"},
        files={"file": ("manual.txt", b"lease recovered source", "text/plain")},
    ).json()
    expired = datetime.now(UTC) - timedelta(seconds=1)
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_messages SET status='processing', lease_owner='dead-worker', "
                "lease_expires_at=:expired WHERE operation_id=:operation"
            ),
            {"expired": expired, "operation": update_response["id"]},
        )
    dry = runtime.lifecycle_repository.reconcile(now=datetime.now(UTC), dry_run=True)
    assert dry.findings[0].kind == "expired_outbox_lease"
    runtime.lifecycle_repository.reconcile(now=datetime.now(UTC), dry_run=False)
    assert runtime.lifecycle_worker.run_once() is True

    with runtime.engine.connect() as connection:
        generations = connection.scalar(
            text("SELECT count(*) FROM index_versions WHERE knowledge_base_id=:kb"),
            {"kb": kb},
        )
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_messages SET status='pending', published_at=NULL, "
                "available_at=now() WHERE operation_id=:operation"
            ),
            {"operation": update_response["id"]},
        )
    assert runtime.lifecycle_worker.run_once() is True
    with runtime.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM index_versions WHERE knowledge_base_id=:kb"),
            {"kb": kb},
        ) == generations

    deleted = client.request(
        "DELETE",
        f"/v1/documents/{document_id}",
        headers=_headers("network-delete"),
        json={"reason": "network failure injection"},
    ).json()

    def fail_projection(
        tenant_id: TenantId, target_document_id: DocumentId, *, deleted: bool
    ) -> None:
        assert str(tenant_id) == TENANT
        assert str(target_document_id) == document_id
        assert deleted is True
        raise TimeoutError("search network unavailable")

    monkeypatch.setattr(runtime.search, "set_document_deleted", fail_projection)
    assert runtime.lifecycle_worker.run_once() is True
    waiting = client.get(
        f"/v1/lifecycle-operations/{deleted['id']}", headers=_headers()
    ).json()
    assert waiting["status"] == "waiting_retry"
    assert waiting["failure_class"] == "transient"
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_messages SET attempts=max_attempts-1, available_at=now() "
                "WHERE operation_id=:operation"
            ),
            {"operation": deleted["id"]},
        )
    assert runtime.lifecycle_worker.run_once() is True
    exhausted = client.get(
        f"/v1/lifecycle-operations/{deleted['id']}", headers=_headers()
    ).json()
    assert exhausted["status"] == "dead_letter"
    assert _query(client, kb, "lease recovered source")["status"] == "no_evidence"


def test_expired_worker_cannot_overwrite_the_new_lease_owner(
    lifecycle_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = lifecycle_client
    _, document_id, _ = _create_and_ingest(client, runtime)
    submitted = client.put(
        f"/v1/documents/{document_id}/content",
        headers={**_headers("lease-race"), "X-Lifecycle-Reason": "lease race"},
        files={"file": ("manual.txt", b"lease race source", "text/plain")},
    ).json()
    now = datetime.now(UTC)
    stale = runtime.lifecycle_repository.claim_outbox(
        worker_id="worker-a", now=now, lease_seconds=1
    )
    assert stale is not None
    current = runtime.lifecycle_repository.claim_outbox(
        worker_id="worker-b", now=now + timedelta(seconds=2), lease_seconds=60
    )
    assert current is not None

    with pytest.raises(LifecycleConflict, match="lease was lost"):
        runtime.lifecycle_repository.retry_message(
            stale,
            decision=FailureDecision(FailureClass.TRANSIENT, True, "network"),
            error_message="stale worker failure",
            available_at=now + timedelta(seconds=3),
            now=now + timedelta(seconds=2),
        )

    operation = client.get(
        f"/v1/lifecycle-operations/{submitted['id']}", headers=_headers()
    ).json()
    assert operation["status"] == "running"
    assert operation["attempts"] == 2
    with runtime.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT lease_owner FROM outbox_messages WHERE operation_id=:operation"),
            {"operation": submitted["id"]},
        ) == "worker-b"
