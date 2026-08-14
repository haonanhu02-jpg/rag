from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from rag_platform.domain.authorization import AuthorizationContext, TrustedPrincipal
from rag_platform.domain.identifiers import (
    ActorId,
    BatchId,
    DocumentId,
    DocumentVersionId,
    JobId,
    KnowledgeBaseId,
    OperationId,
    OutboxMessageId,
    TenantId,
)
from rag_platform.domain.policies import ResourceNotFound
from rag_platform.modules.knowledge.contracts import UnsupportedDocument
from rag_platform.modules.lifecycle.contracts import (
    LifecycleKind,
    LifecycleProjection,
    LifecycleRepository,
    OutboxMessage,
    OutboxStatus,
    ProjectionVersionRecord,
    ReconciliationInventory,
    ReconciliationReport,
)
from rag_platform.modules.lifecycle.service import (
    LifecycleCoordinator,
    LifecycleReconciler,
    LifecycleWorker,
)
from rag_platform.modules.ports import Clock, ObjectStore

NOW = datetime(2026, 8, 14, tzinfo=UTC)
TENANT_ID = TenantId(UUID(int=1))
DOCUMENT_ID = DocumentId(UUID(int=2))
OPERATION_ID = OperationId(UUID(int=3))


def _context() -> AuthorizationContext:
    return AuthorizationContext.from_principal(
        TrustedPrincipal(
            tenant_id=TENANT_ID,
            actor_id=ActorId(UUID(int=4)),
            roles=frozenset({"editor"}),
        )
    )


def _clock() -> MagicMock:
    clock = MagicMock(spec=Clock)
    clock.now.return_value = NOW
    return clock


def _message(event_type: str, payload: dict[str, object]) -> OutboxMessage:
    return OutboxMessage(
        id=OutboxMessageId(UUID(int=5)),
        message_id=f"message-{event_type}",
        tenant_id=TENANT_ID,
        operation_id=OPERATION_ID,
        event_type=event_type,
        aggregate_id=str(DOCUMENT_ID),
        payload=payload,
        status=OutboxStatus.PROCESSING,
        attempts=1,
        max_attempts=3,
        available_at=NOW,
    )


def test_coordinator_rejects_invalid_uploads_and_reasons() -> None:
    repository = MagicMock(spec=LifecycleRepository)
    coordinator = LifecycleCoordinator(
        repository=repository,
        object_store=MagicMock(spec=ObjectStore),
        clock=_clock(),
        max_upload_bytes=4,
    )

    for content in (b"", b"12345"):
        with pytest.raises(UnsupportedDocument, match="upload limit"):
            coordinator.update(
                _context(),
                document_id=DOCUMENT_ID,
                file_name="source.txt",
                media_type="text/plain",
                content=content,
                idempotency_key="update-1",
                reason="change",
            )

    for reason in ("   ", "x" * 1001):
        with pytest.raises(ValueError, match=r"1\.\.1000"):
            coordinator.reparse(
                _context(),
                document_id=DOCUMENT_ID,
                idempotency_key="reparse-1",
                reason=reason,
            )
    repository.register_update.assert_not_called()


def test_coordinator_reports_missing_operation_and_batch() -> None:
    repository = MagicMock(spec=LifecycleRepository)
    repository.get_operation.return_value = None
    repository.get_batch.return_value = None
    coordinator = LifecycleCoordinator(
        repository=repository,
        object_store=MagicMock(spec=ObjectStore),
        clock=_clock(),
        max_upload_bytes=4,
    )

    with pytest.raises(ResourceNotFound, match="operation"):
        coordinator.get(_context(), OPERATION_ID)
    with pytest.raises(ResourceNotFound, match="batch"):
        coordinator.get_batch(_context(), BatchId(UUID(int=6)))

    batch = MagicMock()
    repository.get_batch.return_value = batch
    assert coordinator.get_batch(_context(), BatchId(UUID(int=6))) is batch


def test_coordinator_caps_batch_concurrency_and_rejects_non_positive_values() -> None:
    repository = MagicMock(spec=LifecycleRepository)
    coordinator = LifecycleCoordinator(
        repository=repository,
        object_store=MagicMock(spec=ObjectStore),
        clock=_clock(),
        max_upload_bytes=4,
        max_batch_concurrency=3,
    )
    operation_ids = (OPERATION_ID,)

    coordinator.create_batch(
        _context(),
        knowledge_base_id=KnowledgeBaseId(UUID(int=9)),
        kind=LifecycleKind.REBUILD,
        operation_ids=operation_ids,
        idempotency_key="batch-1",
        concurrency=99,
    )
    assert repository.create_batch.call_args.kwargs["concurrency"] == 3

    with pytest.raises(ValueError, match="positive"):
        coordinator.create_batch(
            _context(),
            knowledge_base_id=KnowledgeBaseId(UUID(int=9)),
            kind=LifecycleKind.REBUILD,
            operation_ids=operation_ids,
            idempotency_key="batch-2",
            concurrency=0,
        )


@pytest.mark.parametrize(
    ("message", "job_id"),
    [
        (_message("ingestion.requested", {}), None),
        (
            _message(
                "document.rolled_back",
                {
                    "tenant_id": str(TENANT_ID),
                    "document_id": str(DOCUMENT_ID),
                    "document_version_id": str(DocumentVersionId(UUID(int=7))),
                },
            ),
            None,
        ),
        (_message("document.deleted", {}), None),
        (_message("unsupported.event", {"tenant_id": str(TENANT_ID)}), None),
    ],
)
def test_worker_classifies_malformed_or_unsupported_messages(
    message: OutboxMessage, job_id: JobId | None
) -> None:
    repository = MagicMock(spec=LifecycleRepository)
    repository.claim_outbox.return_value = message
    repository.job_for_operation.return_value = job_id
    worker = LifecycleWorker(
        repository=repository,
        ingestion=MagicMock(),
        projection=MagicMock(spec=LifecycleProjection),
        object_store=MagicMock(spec=ObjectStore),
        clock=_clock(),
        worker_id="worker-1",
    )

    assert worker.run_once() is True
    repository.retry_message.assert_called_once()
    retry = repository.retry_message.call_args.kwargs
    assert retry["available_at"] is None
    repository.complete_message.assert_not_called()


class _SilentError(RuntimeError):
    def __str__(self) -> str:
        return ""


def test_worker_records_exception_type_when_error_has_no_message() -> None:
    repository = MagicMock(spec=LifecycleRepository)
    repository.claim_outbox.return_value = _message("ingestion.requested", {})
    repository.job_for_operation.return_value = JobId(UUID(int=8))
    ingestion = MagicMock()
    ingestion.run.side_effect = _SilentError()
    worker = LifecycleWorker(
        repository=repository,
        ingestion=ingestion,
        projection=MagicMock(spec=LifecycleProjection),
        object_store=MagicMock(spec=ObjectStore),
        clock=_clock(),
        worker_id="worker-1",
    )

    assert worker.run_once() is True
    assert repository.retry_message.call_args.kwargs["error_message"] == "_SilentError"


def test_worker_purges_projection_database_and_every_object() -> None:
    repository = MagicMock(spec=LifecycleRepository)
    repository.claim_outbox.return_value = _message(
        "document.purge_requested",
        {"tenant_id": str(TENANT_ID), "document_id": str(DOCUMENT_ID)},
    )
    repository.purge_document.return_value = ("source/a", "source/b")
    projection = MagicMock(spec=LifecycleProjection)
    object_store = MagicMock(spec=ObjectStore)
    worker = LifecycleWorker(
        repository=repository,
        ingestion=MagicMock(),
        projection=projection,
        object_store=object_store,
        clock=_clock(),
        worker_id="worker-1",
    )

    assert worker.run_once() is True
    projection.purge_document.assert_called_once_with(TENANT_ID, DOCUMENT_ID)
    assert object_store.delete.call_count == 2
    repository.complete_message.assert_called_once()


def test_reconciler_reports_drift_and_only_repairs_proven_orphans() -> None:
    repository = MagicMock(spec=LifecycleRepository)
    expected_version = DocumentVersionId(UUID(int=10))
    orphan_version = DocumentVersionId(UUID(int=11))
    repository.reconcile.return_value = ReconciliationReport(True, (), NOW)
    repository.reconciliation_inventory.return_value = ReconciliationInventory(
        frozenset({(TENANT_ID, "expected/source.txt")}),
        (ProjectionVersionRecord(TENANT_ID, expected_version, 2),),
    )
    object_store = MagicMock(spec=ObjectStore)
    object_store.list_objects.return_value = ((TENANT_ID, "orphan/source.txt"),)
    projection = MagicMock(spec=LifecycleProjection)
    projection.list_projection_versions.return_value = (
        ProjectionVersionRecord(TENANT_ID, expected_version, 1),
        ProjectionVersionRecord(TENANT_ID, orphan_version, 1),
    )
    reconciler = LifecycleReconciler(
        repository=repository,
        projection=projection,
        object_store=object_store,
        clock=_clock(),
    )

    dry_run = reconciler.run()
    assert {item.kind for item in dry_run.findings} == {
        "missing_object",
        "orphan_object",
        "projection_drift",
        "orphan_projection",
    }
    object_store.delete.assert_not_called()
    projection.delete_document_version.assert_not_called()

    applied = reconciler.run(dry_run=False)
    assert {item.kind for item in applied.findings if item.repaired} == {
        "orphan_object",
        "orphan_projection",
    }
    object_store.delete.assert_called_once_with(
        tenant_id=TENANT_ID, key="orphan/source.txt"
    )
    projection.delete_document_version.assert_called_once_with(TENANT_ID, orphan_version)
