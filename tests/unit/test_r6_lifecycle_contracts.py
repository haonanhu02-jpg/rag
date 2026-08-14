from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from rag_platform.domain.identifiers import (
    ActorId,
    KnowledgeBaseId,
    OperationId,
    TenantId,
)
from rag_platform.modules.lifecycle.contracts import (
    FailureClass,
    LifecycleCancelled,
    LifecycleConflict,
    LifecycleKind,
    LifecycleOperationRecord,
    LifecycleStatus,
    RetryPolicy,
    classify_failure,
)


def test_retry_policy_is_bounded_and_failure_classification_is_explicit() -> None:
    policy = RetryPolicy(max_attempts=4, base_seconds=2, max_seconds=5)
    assert [policy.delay_seconds(value) for value in range(1, 5)] == [2, 4, 5, 5]
    assert classify_failure(TimeoutError()).classification is FailureClass.TRANSIENT
    assert classify_failure(LifecycleConflict("race")).classification is FailureClass.CONCURRENCY
    assert classify_failure(ValueError("bad input")).classification is FailureClass.PERMANENT
    assert classify_failure(RuntimeError("unknown")).classification is FailureClass.UNKNOWN


def test_retry_policy_rejects_non_positive_attempt() -> None:
    try:
        RetryPolicy().delay_seconds(0)
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("non-positive retry attempt was accepted")


class _HttpError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_failure_classification_covers_cancellation_and_http_statuses() -> None:
    assert classify_failure(LifecycleCancelled()).classification is FailureClass.CANCELLED

    retryable = classify_failure(_HttpError(503))
    assert retryable.classification is FailureClass.TRANSIENT
    assert retryable.retryable is True
    assert retryable.code == "http_503"

    permanent = classify_failure(_HttpError(404))
    assert permanent.classification is FailureClass.PERMANENT
    assert permanent.retryable is False
    assert permanent.code == "http_404"


def test_lifecycle_operation_rejects_invalid_progress_and_freezes_metadata() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    values = {
        "id": OperationId(UUID(int=1)),
        "tenant_id": TenantId(UUID(int=2)),
        "knowledge_base_id": KnowledgeBaseId(UUID(int=3)),
        "document_id": None,
        "document_version_id": None,
        "requested_by": ActorId(UUID(int=4)),
        "kind": LifecycleKind.REBUILD,
        "idempotency_key": "rebuild-1",
        "reason": "refresh",
        "status": LifecycleStatus.PENDING,
        "attempts": 0,
        "fencing_token": 0,
        "created_at": now,
        "updated_at": now,
    }
    with pytest.raises(ValueError, match="progress"):
        LifecycleOperationRecord(progress=1.1, **values)  # type: ignore[arg-type]

    metadata = {"source": "api"}
    operation = LifecycleOperationRecord(progress=0.0, metadata=metadata, **values)  # type: ignore[arg-type]
    metadata["source"] = "mutated"
    assert operation.metadata == {"source": "api"}
    with pytest.raises(TypeError):
        operation.metadata["source"] = "blocked"
