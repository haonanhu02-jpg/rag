"""Lifecycle coordinator and at-least-once Outbox worker."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import (
    BatchId,
    DocumentId,
    DocumentVersionId,
    Identifier,
    JobId,
    KnowledgeBaseId,
    OperationId,
    TenantId,
)
from rag_platform.domain.policies import CorePolicies, ResourceNotFound
from rag_platform.modules.knowledge.compiler import DocumentFormatRouter
from rag_platform.modules.knowledge.contracts import IngestionJobRecord, UnsupportedDocument
from rag_platform.modules.lifecycle.contracts import (
    LifecycleBatchRecord,
    LifecycleKind,
    LifecycleOperationRecord,
    LifecycleProjection,
    LifecycleRepository,
    LifecycleSubmission,
    ReconciliationFinding,
    ReconciliationReport,
    RetryPolicy,
    classify_failure,
)
from rag_platform.modules.ports import Clock, ObjectStore


class IngestionRunner(Protocol):
    def run(self, job_id: JobId, *, record_failure: bool = True) -> IngestionJobRecord: ...


class LifecycleCoordinator:
    """Owns lifecycle commands; PostgreSQL is authoritative before any projection side effect."""

    def __init__(
        self,
        *,
        repository: LifecycleRepository,
        object_store: ObjectStore,
        clock: Clock,
        max_upload_bytes: int,
        delete_retention_seconds: int = 7 * 24 * 60 * 60,
        max_batch_concurrency: int = 3,
    ) -> None:
        self._repository = repository
        self._object_store = object_store
        self._clock = clock
        self._max_upload_bytes = max_upload_bytes
        self._delete_retention_seconds = delete_retention_seconds
        self._max_batch_concurrency = max_batch_concurrency

    def update(
        self,
        context: AuthorizationContext,
        *,
        document_id: DocumentId,
        file_name: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
        reason: str,
        chunk_method: str = "general",
    ) -> LifecycleSubmission:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        if not 0 < len(content) <= self._max_upload_bytes:
            raise UnsupportedDocument("document is empty or exceeds the upload limit")
        normalized_media_type = media_type.split(";", maxsplit=1)[0].strip().lower()
        DocumentFormatRouter.resolve(
            file_name=file_name,
            media_type=normalized_media_type,
            content=content,
        )
        digest = hashlib.sha256(content).hexdigest()
        object_key = f"versions/{document_id}/{digest[:16]}"
        self._object_store.put(tenant_id=context.tenant_id, key=object_key, value=content)
        return self._repository.register_update(
            context=context,
            document_id=document_id,
            file_name=file_name,
            media_type=normalized_media_type,
            object_key=object_key,
            source_sha256=digest,
            size_bytes=len(content),
            chunk_method=chunk_method,
            kind=LifecycleKind.UPDATE,
            idempotency_key=idempotency_key,
            reason=_reason(reason),
            now=self._now(),
        )

    def reparse(
        self,
        context: AuthorizationContext,
        *,
        document_id: DocumentId,
        idempotency_key: str,
        reason: str,
        chunk_method: str | None = None,
    ) -> LifecycleSubmission:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        return self._repository.register_update(
            context=context,
            document_id=document_id,
            file_name=None,
            media_type=None,
            object_key=None,
            source_sha256=None,
            size_bytes=None,
            chunk_method=chunk_method,
            kind=LifecycleKind.REPARSE,
            idempotency_key=idempotency_key,
            reason=_reason(reason),
            now=self._now(),
        )

    def delete(
        self,
        context: AuthorizationContext,
        *,
        document_id: DocumentId,
        idempotency_key: str,
        reason: str,
    ) -> LifecycleSubmission:
        return self._transition(
            context,
            kind=LifecycleKind.DELETE,
            document_id=document_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def restore(
        self,
        context: AuthorizationContext,
        *,
        document_id: DocumentId,
        idempotency_key: str,
        reason: str,
    ) -> LifecycleSubmission:
        return self._transition(
            context,
            kind=LifecycleKind.RESTORE,
            document_id=document_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def rollback(
        self,
        context: AuthorizationContext,
        *,
        document_id: DocumentId,
        target_version_id: DocumentVersionId,
        idempotency_key: str,
        reason: str,
    ) -> LifecycleSubmission:
        return self._transition(
            context,
            kind=LifecycleKind.ROLLBACK,
            document_id=document_id,
            target_version_id=target_version_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def rebuild(
        self,
        context: AuthorizationContext,
        *,
        knowledge_base_id: KnowledgeBaseId,
        idempotency_key: str,
        reason: str,
    ) -> LifecycleSubmission:
        return self._transition(
            context,
            kind=LifecycleKind.REBUILD,
            knowledge_base_id=knowledge_base_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def get(
        self, context: AuthorizationContext, operation_id: OperationId
    ) -> LifecycleOperationRecord:
        operation = self._repository.get_operation(context, operation_id)
        if operation is None:
            raise ResourceNotFound("lifecycle operation not found")
        return operation

    def cancel(
        self, context: AuthorizationContext, operation_id: OperationId
    ) -> LifecycleOperationRecord:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        return self._repository.cancel_operation(context, operation_id, self._now())

    def create_batch(
        self,
        context: AuthorizationContext,
        *,
        knowledge_base_id: KnowledgeBaseId,
        kind: LifecycleKind,
        operation_ids: tuple[OperationId, ...],
        idempotency_key: str,
        concurrency: int | None = None,
    ) -> LifecycleBatchRecord:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        requested_concurrency = self._max_batch_concurrency if concurrency is None else concurrency
        if requested_concurrency < 1:
            raise ValueError("batch concurrency must be positive")
        return self._repository.create_batch(
            context=context,
            knowledge_base_id=knowledge_base_id,
            kind=kind,
            operation_ids=operation_ids,
            idempotency_key=idempotency_key,
            concurrency=min(requested_concurrency, self._max_batch_concurrency),
            now=self._now(),
        )

    def get_batch(self, context: AuthorizationContext, batch_id: BatchId) -> LifecycleBatchRecord:
        value = self._repository.get_batch(context, batch_id)
        if value is None:
            raise ResourceNotFound("lifecycle batch not found")
        return value

    def _transition(
        self,
        context: AuthorizationContext,
        *,
        kind: LifecycleKind,
        document_id: DocumentId | None = None,
        knowledge_base_id: KnowledgeBaseId | None = None,
        target_version_id: DocumentVersionId | None = None,
        idempotency_key: str,
        reason: str,
    ) -> LifecycleSubmission:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        return self._repository.request_transition(
            context=context,
            kind=kind,
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            target_version_id=target_version_id,
            idempotency_key=idempotency_key,
            reason=_reason(reason),
            retention_seconds=self._delete_retention_seconds,
            now=self._now(),
        )

    def _now(self) -> datetime:
        return self._clock.now()


class LifecycleReconciler:
    """Compare PostgreSQL facts with object and search projections."""

    def __init__(
        self,
        *,
        repository: LifecycleRepository,
        projection: LifecycleProjection,
        object_store: ObjectStore,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._projection = projection
        self._object_store = object_store
        self._clock = clock

    def run(self, *, dry_run: bool = True) -> ReconciliationReport:
        now = self._clock.now()
        authoritative = self._repository.reconcile(now=now, dry_run=dry_run)
        inventory = self._repository.reconciliation_inventory()
        findings = list(authoritative.findings)

        actual_objects = frozenset(self._object_store.list_objects())
        for tenant_id, key in sorted(
            inventory.object_keys - actual_objects, key=lambda item: (str(item[0]), item[1])
        ):
            findings.append(
                ReconciliationFinding(
                    "missing_object",
                    f"{tenant_id}:{key}",
                    False,
                    False,
                    "authoritative document version has no source object",
                )
            )
        for tenant_id, key in sorted(
            actual_objects - inventory.object_keys, key=lambda item: (str(item[0]), item[1])
        ):
            repaired = not dry_run
            if repaired:
                self._object_store.delete(tenant_id=tenant_id, key=key)
            findings.append(
                ReconciliationFinding(
                    "orphan_object",
                    f"{tenant_id}:{key}",
                    True,
                    repaired,
                    "object is not referenced by an authoritative version",
                )
            )

        expected_projections = {
            (item.tenant_id, item.document_version_id): item.chunk_count
            for item in inventory.projection_versions
        }
        actual_projections = {
            (item.tenant_id, item.document_version_id): item.chunk_count
            for item in self._projection.list_projection_versions()
        }
        for projection_key, expected_count in sorted(
            expected_projections.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        ):
            actual_count = actual_projections.get(projection_key, 0)
            if actual_count != expected_count:
                findings.append(
                    ReconciliationFinding(
                        "projection_drift",
                        f"{projection_key[0]}:{projection_key[1]}",
                        False,
                        False,
                        f"expected_chunks={expected_count}, actual_chunks={actual_count}",
                    )
                )
        for tenant_id, version_id in sorted(
            actual_projections.keys() - expected_projections.keys(),
            key=lambda item: (str(item[0]), str(item[1])),
        ):
            repaired = not dry_run
            if repaired:
                self._projection.delete_document_version(tenant_id, version_id)
            findings.append(
                ReconciliationFinding(
                    "orphan_projection",
                    f"{tenant_id}:{version_id}",
                    True,
                    repaired,
                    "projection version has no authoritative chunks",
                )
            )
        return ReconciliationReport(dry_run, tuple(findings), now)


class LifecycleWorker:
    """Claims one deterministic Outbox message and executes an idempotent side effect."""

    def __init__(
        self,
        *,
        repository: LifecycleRepository,
        ingestion: IngestionRunner,
        projection: LifecycleProjection,
        object_store: ObjectStore,
        clock: Clock,
        worker_id: str,
        retry_policy: RetryPolicy | None = None,
        lease_seconds: int = 60,
    ) -> None:
        self._repository = repository
        self._ingestion = ingestion
        self._projection = projection
        self._object_store = object_store
        self._clock = clock
        self._worker_id = worker_id
        self._retry_policy = retry_policy or RetryPolicy()
        self._lease_seconds = lease_seconds

    def run_once(self) -> bool:
        now = self._clock.now()
        message = self._repository.claim_outbox(
            worker_id=self._worker_id,
            now=now,
            lease_seconds=self._lease_seconds,
        )
        if message is None:
            return False
        try:
            self._dispatch(message.event_type, message.operation_id, message.payload)
        except Exception as error:
            decision = classify_failure(error)
            retryable = decision.retryable and message.attempts < message.max_attempts
            available_at = (
                now + timedelta(seconds=self._retry_policy.delay_seconds(message.attempts))
                if retryable
                else None
            )
            self._repository.retry_message(
                message,
                decision=decision,
                error_message=str(error)[:1000] or type(error).__name__,
                available_at=available_at,
                now=self._clock.now(),
            )
            return True
        self._repository.complete_message(message, self._clock.now())
        return True

    def _dispatch(
        self, event_type: str, operation_id: OperationId, payload: Mapping[str, object]
    ) -> None:
        if event_type == "ingestion.requested":
            job_id = self._repository.job_for_operation(operation_id)
            if job_id is None:
                raise FileNotFoundError("operation ingestion job is missing")
            self._ingestion.run(job_id, record_failure=False)
            return
        tenant_id = _payload_id(payload, "tenant_id", TenantId)
        if event_type in {"document.deleted", "document.restored"}:
            document_id = _payload_id(payload, "document_id", DocumentId)
            self._projection.set_document_deleted(
                tenant_id,
                document_id,
                deleted=event_type == "document.deleted",
            )
            return
        if event_type == "document.rolled_back":
            document_id = _payload_id(payload, "document_id", DocumentId)
            version_id = _payload_id(payload, "document_version_id", DocumentVersionId)
            index_version_id = payload.get("index_version_id")
            if not isinstance(index_version_id, str):
                raise ValueError("rollback payload is missing index_version_id")
            self._projection.activate_document_version(
                tenant_id,
                document_id,
                version_id,
                index_version_id,
            )
            return
        if event_type == "index.rebuild_requested":
            self._repository.execute_rebuild(operation_id, self._clock.now())
            return
        if event_type == "document.purge_requested":
            document_id = _payload_id(payload, "document_id", DocumentId)
            self._projection.purge_document(tenant_id, document_id)
            keys = self._repository.purge_document(operation_id, self._clock.now())
            for key in keys:
                self._object_store.delete(tenant_id=tenant_id, key=key)
            return
        raise ValueError(f"unknown lifecycle event: {event_type}")


def _reason(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 1000:
        raise ValueError("lifecycle reason must contain 1..1000 characters")
    return normalized


def _payload_id[IdentifierT: Identifier](
    payload: Mapping[str, object], name: str, kind: type[IdentifierT]
) -> IdentifierT:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"outbox payload is missing {name}")
    return kind(UUID(value))
