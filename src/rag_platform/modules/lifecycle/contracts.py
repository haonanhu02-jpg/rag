"""Framework-neutral lifecycle state, retry, and persistence contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from rag_platform.domain.authorization import AuthorizationContext
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
from rag_platform.modules.knowledge.contracts import IngestionJobRecord


class LifecycleKind(StrEnum):
    INGEST = "ingest"
    UPDATE = "update"
    REPARSE = "reparse"
    ROLLBACK = "rollback"
    DELETE = "delete"
    RESTORE = "restore"
    PURGE = "purge"
    REBUILD = "rebuild"
    RECONCILE = "reconcile"


class LifecycleStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_RETRY = "waiting_retry"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


TERMINAL_STATUSES = frozenset(
    {
        LifecycleStatus.CANCELLED,
        LifecycleStatus.SUCCEEDED,
        LifecycleStatus.FAILED,
        LifecycleStatus.DEAD_LETTER,
    }
)


class FailureClass(StrEnum):
    TRANSIENT = "transient"
    CONCURRENCY = "concurrency"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class LifecycleConflict(ValueError):
    """A command conflicts with the current authoritative revision."""


class LifecycleCancelled(RuntimeError):
    """A cancellation request won a race before the side effect committed."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 6
    concurrency_attempts: int = 3
    base_seconds: float = 1.0
    max_seconds: float = 300.0

    def delay_seconds(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return float(min(self.max_seconds, self.base_seconds * (2 ** (attempt - 1))))


@dataclass(frozen=True, slots=True)
class FailureDecision:
    classification: FailureClass
    retryable: bool
    code: str


def classify_failure(error: BaseException) -> FailureDecision:
    if isinstance(error, LifecycleCancelled):
        return FailureDecision(FailureClass.CANCELLED, False, "cancelled")
    if isinstance(error, LifecycleConflict):
        return FailureDecision(FailureClass.CONCURRENCY, True, "lifecycle_conflict")
    if isinstance(error, (ConnectionError, TimeoutError)):
        return FailureDecision(FailureClass.TRANSIENT, True, type(error).__name__)
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        retryable = status_code in {408, 409, 425, 429} or status_code >= 500
        return FailureDecision(
            FailureClass.TRANSIENT if retryable else FailureClass.PERMANENT,
            retryable,
            f"http_{status_code}",
        )
    if isinstance(error, (FileNotFoundError, ValueError)):
        return FailureDecision(FailureClass.PERMANENT, False, type(error).__name__)
    return FailureDecision(FailureClass.UNKNOWN, False, type(error).__name__)


@dataclass(frozen=True, slots=True)
class LifecycleOperationRecord:
    id: OperationId
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId | None
    document_version_id: DocumentVersionId | None
    requested_by: ActorId
    kind: LifecycleKind
    idempotency_key: str
    reason: str
    status: LifecycleStatus
    progress: float
    attempts: int
    fencing_token: int
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime | None = None
    purge_after: datetime | None = None
    failure_class: FailureClass | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.progress <= 1:
            raise ValueError("progress must be within [0, 1]")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class LifecycleSubmission:
    operation: LifecycleOperationRecord
    job: IngestionJobRecord | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: OutboxMessageId
    message_id: str
    tenant_id: TenantId
    operation_id: OperationId
    event_type: str
    aggregate_id: str
    payload: Mapping[str, object]
    status: OutboxStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class LifecycleBatchRecord:
    id: BatchId
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    requested_by: ActorId
    kind: LifecycleKind
    idempotency_key: str
    concurrency: int
    operation_ids: tuple[OperationId, ...]
    status: str
    succeeded: int
    failed: int
    cancelled: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    kind: str
    resource_id: str
    safe_to_repair: bool
    repaired: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    dry_run: bool
    findings: tuple[ReconciliationFinding, ...]
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectionVersionRecord:
    tenant_id: TenantId
    document_version_id: DocumentVersionId
    chunk_count: int


@dataclass(frozen=True, slots=True)
class ReconciliationInventory:
    object_keys: frozenset[tuple[TenantId, str]]
    projection_versions: tuple[ProjectionVersionRecord, ...]


class LifecycleRepository(Protocol):
    def register_update(
        self,
        *,
        context: AuthorizationContext,
        document_id: DocumentId,
        file_name: str | None,
        media_type: str | None,
        object_key: str | None,
        source_sha256: str | None,
        size_bytes: int | None,
        chunk_method: str | None,
        kind: LifecycleKind,
        idempotency_key: str,
        reason: str,
        now: datetime,
    ) -> LifecycleSubmission: ...

    def request_transition(
        self,
        *,
        context: AuthorizationContext,
        kind: LifecycleKind,
        document_id: DocumentId | None,
        knowledge_base_id: KnowledgeBaseId | None,
        target_version_id: DocumentVersionId | None,
        idempotency_key: str,
        reason: str,
        retention_seconds: int,
        now: datetime,
    ) -> LifecycleSubmission: ...

    def get_operation(
        self, context: AuthorizationContext, operation_id: OperationId
    ) -> LifecycleOperationRecord | None: ...

    def cancel_operation(
        self, context: AuthorizationContext, operation_id: OperationId, now: datetime
    ) -> LifecycleOperationRecord: ...

    def claim_outbox(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> OutboxMessage | None: ...

    def complete_message(self, message: OutboxMessage, now: datetime) -> None: ...

    def retry_message(
        self,
        message: OutboxMessage,
        *,
        decision: FailureDecision,
        error_message: str,
        available_at: datetime | None,
        now: datetime,
    ) -> None: ...

    def job_for_operation(self, operation_id: OperationId) -> JobId | None: ...

    def execute_rebuild(self, operation_id: OperationId, now: datetime) -> None: ...

    def purge_document(self, operation_id: OperationId, now: datetime) -> tuple[str, ...]: ...

    def reconcile(self, *, now: datetime, dry_run: bool) -> ReconciliationReport: ...

    def reconciliation_inventory(self) -> ReconciliationInventory: ...

    def create_batch(
        self,
        *,
        context: AuthorizationContext,
        knowledge_base_id: KnowledgeBaseId,
        kind: LifecycleKind,
        operation_ids: tuple[OperationId, ...],
        idempotency_key: str,
        concurrency: int,
        now: datetime,
    ) -> LifecycleBatchRecord: ...

    def get_batch(
        self, context: AuthorizationContext, batch_id: BatchId
    ) -> LifecycleBatchRecord | None: ...


class LifecycleProjection(Protocol):
    def set_document_deleted(
        self, tenant_id: TenantId, document_id: DocumentId, *, deleted: bool
    ) -> None: ...

    def purge_document(self, tenant_id: TenantId, document_id: DocumentId) -> None: ...

    def activate_document_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        document_version_id: DocumentVersionId,
        index_version_id: str,
    ) -> None: ...

    def list_projection_versions(self) -> tuple[ProjectionVersionRecord, ...]: ...

    def delete_document_version(
        self, tenant_id: TenantId, document_version_id: DocumentVersionId
    ) -> None: ...
