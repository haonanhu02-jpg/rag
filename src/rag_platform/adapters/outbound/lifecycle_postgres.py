"""PostgreSQL authority for reliable lifecycle operations and transactional Outbox."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid5

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine

from rag_platform.adapters.outbound.postgres import (
    _STABLE_NAMESPACE,
    chunk_embeddings,
    document_blocks,
    document_chunks,
    document_versions,
    documents,
    index_routes,
    index_versions,
    ingestion_jobs,
    ingestion_tasks,
    knowledge_bases,
    lifecycle_batches,
    lifecycle_operations,
    outbox_messages,
    upload_idempotency_keys,
)
from rag_platform.domain.authorization import AuthorizationContext, TrustedPrincipal
from rag_platform.domain.entities import WorkStatus
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
    TraceId,
)
from rag_platform.domain.policies import AccessDenied, CorePolicies, ResourceNotFound
from rag_platform.modules.knowledge.contracts import (
    IdempotencyConflict,
    IngestionJobRecord,
)
from rag_platform.modules.lifecycle.contracts import (
    TERMINAL_STATUSES,
    FailureClass,
    FailureDecision,
    LifecycleBatchRecord,
    LifecycleConflict,
    LifecycleKind,
    LifecycleOperationRecord,
    LifecycleStatus,
    LifecycleSubmission,
    OutboxMessage,
    OutboxStatus,
    ProjectionVersionRecord,
    ReconciliationFinding,
    ReconciliationInventory,
    ReconciliationReport,
)

_TASKS = ("load", "compile", "embed", "stage", "project", "validate", "publish")


class PostgresLifecycleRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

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
    ) -> LifecycleSubmission:
        if kind not in {LifecycleKind.UPDATE, LifecycleKind.REPARSE}:
            raise ValueError("register_update only accepts update or reparse")
        fingerprint = _fingerprint(
            kind.value,
            str(document_id),
            file_name or "active-file",
            media_type or "active-media-type",
            source_sha256 or "active-source",
            str(size_bytes) if size_bytes is not None else "active-size",
            chunk_method or "active-method",
            reason,
        )
        with self._engine.begin() as connection:
            duplicate = self._duplicate(connection, context, idempotency_key, fingerprint)
            if duplicate is not None:
                return duplicate
            document = self._document_for_write(connection, context, document_id)
            if document["deleted_at"] is not None:
                raise LifecycleConflict("deleted documents must be restored before update")
            active = (
                connection.execute(
                    select(document_versions)
                    .where(
                        document_versions.c.tenant_id == context.tenant_id.value,
                        document_versions.c.document_id == document_id.value,
                        document_versions.c.status == "active",
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if active is None:
                raise LifecycleConflict("document has no active version")
            if kind is LifecycleKind.REPARSE:
                file_name = str(active["file_name"])
                media_type = str(active["media_type"])
                object_key = str(active["object_key"])
                source_sha256 = str(active["source_sha256"])
                size_bytes = int(active["size_bytes"])
                chunk_method = chunk_method or str(active["chunk_method"])
            if None in {
                file_name,
                media_type,
                object_key,
                source_sha256,
                size_bytes,
                chunk_method,
            }:
                raise ValueError("update source metadata is incomplete")
            operation_id = _operation_id(context.tenant_id, idempotency_key)
            version_id = DocumentVersionId(uuid5(_STABLE_NAMESPACE, f"version:{operation_id}"))
            job_id = JobId(uuid5(_STABLE_NAMESPACE, f"job:{operation_id}"))
            revision = cast(
                int,
                connection.scalar(
                    select(func.max(document_versions.c.revision) + 1).where(
                        document_versions.c.document_id == document_id.value
                    )
                ),
            )
            fencing_token = int(document["revision_token"]) + 1
            changed = connection.execute(
                update(documents)
                .where(
                    documents.c.id == document_id.value,
                    documents.c.tenant_id == context.tenant_id.value,
                    documents.c.revision_token == document["revision_token"],
                )
                .values(revision_token=fencing_token)
            )
            if changed.rowcount != 1:
                raise LifecycleConflict("document revision changed concurrently")
            connection.execute(
                insert(document_versions).values(
                    id=version_id.value,
                    tenant_id=context.tenant_id.value,
                    document_id=document_id.value,
                    revision=revision,
                    source_sha256=source_sha256,
                    status="draft",
                    created_at=now,
                    file_name=file_name,
                    media_type=media_type,
                    object_key=object_key,
                    size_bytes=size_bytes,
                    chunk_method=chunk_method,
                )
            )
            self._insert_operation(
                connection,
                operation_id=operation_id,
                context=context,
                knowledge_base_id=KnowledgeBaseId(document["knowledge_base_id"]),
                document_id=document_id,
                version_id=version_id,
                kind=kind,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                reason=reason,
                fencing_token=fencing_token,
                metadata={"job_id": str(job_id), "previous_version_id": str(active["id"])},
                now=now,
            )
            self._insert_ingestion(
                connection,
                context=context,
                knowledge_base_id=KnowledgeBaseId(document["knowledge_base_id"]),
                document_id=document_id,
                version_id=version_id,
                operation_id=operation_id,
                job_id=job_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                now=now,
            )
            self._insert_outbox(
                connection,
                context.tenant_id,
                operation_id,
                "ingestion.requested",
                str(document_id),
                {
                    "tenant_id": str(context.tenant_id),
                    "knowledge_base_id": str(document["knowledge_base_id"]),
                    "document_id": str(document_id),
                    "job_id": str(job_id),
                },
                now,
            )
            operation = self._operation(
                connection.execute(
                    select(lifecycle_operations).where(
                        lifecycle_operations.c.id == operation_id.value
                    )
                ).mappings().one()
            )
            job = self._job(
                connection.execute(
                    select(ingestion_jobs).where(ingestion_jobs.c.id == job_id.value)
                ).mappings().one()
            )
            return LifecycleSubmission(operation, job)

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
    ) -> LifecycleSubmission:
        if kind not in {
            LifecycleKind.DELETE,
            LifecycleKind.RESTORE,
            LifecycleKind.ROLLBACK,
            LifecycleKind.REBUILD,
        }:
            raise ValueError("unsupported lifecycle transition")
        fingerprint = _fingerprint(
            kind.value,
            str(document_id or ""),
            str(knowledge_base_id or ""),
            str(target_version_id or ""),
            reason,
        )
        with self._engine.begin() as connection:
            duplicate = self._duplicate(connection, context, idempotency_key, fingerprint)
            if duplicate is not None:
                return duplicate
            if kind is LifecycleKind.REBUILD:
                if knowledge_base_id is None:
                    raise ValueError("rebuild requires a knowledge base")
                self._knowledge_base_for_write(connection, context, knowledge_base_id)
                operation_id = _operation_id(context.tenant_id, idempotency_key)
                route = connection.execute(
                    select(index_routes).where(
                        index_routes.c.tenant_id == context.tenant_id.value,
                        index_routes.c.knowledge_base_id == knowledge_base_id.value,
                    )
                ).mappings().one_or_none()
                fencing = 0 if route is None else int(route["fencing_token"])
                self._insert_operation(
                    connection,
                    operation_id=operation_id,
                    context=context,
                    knowledge_base_id=knowledge_base_id,
                    document_id=None,
                    version_id=None,
                    kind=kind,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    reason=reason,
                    fencing_token=fencing,
                    metadata={},
                    now=now,
                )
                self._insert_outbox(
                    connection,
                    context.tenant_id,
                    operation_id,
                    "index.rebuild_requested",
                    str(knowledge_base_id),
                    {
                        "tenant_id": str(context.tenant_id),
                        "knowledge_base_id": str(knowledge_base_id),
                    },
                    now,
                )
                return LifecycleSubmission(self._load_operation(connection, operation_id))
            if document_id is None:
                raise ValueError("document transition requires document_id")
            document = self._document_for_write(connection, context, document_id)
            knowledge_base_id = KnowledgeBaseId(document["knowledge_base_id"])
            operation_id = _operation_id(context.tenant_id, idempotency_key)
            active = connection.execute(
                select(document_versions)
                .where(
                    document_versions.c.document_id == document_id.value,
                    document_versions.c.status == "active",
                )
                .with_for_update()
            ).mappings().one_or_none()
            fencing = int(document["revision_token"]) + 1
            metadata: dict[str, object] = {}
            status = LifecycleStatus.PENDING
            progress = 0.0
            event_type: str | None = None
            event_payload: dict[str, object] = {
                "tenant_id": str(context.tenant_id),
                "document_id": str(document_id),
            }
            version_id: DocumentVersionId | None = None
            purge_after: datetime | None = None
            if kind is LifecycleKind.DELETE:
                if document["deleted_at"] is not None or active is None:
                    raise LifecycleConflict("document is already deleted or has no active version")
                version_id = DocumentVersionId(active["id"])
                purge_after = now + timedelta(seconds=retention_seconds)
                metadata["previous_version_id"] = str(version_id)
                connection.execute(
                    update(documents)
                    .where(documents.c.id == document_id.value)
                    .values(deleted_at=now, purge_after=purge_after, revision_token=fencing)
                )
                connection.execute(
                    update(document_versions)
                    .where(document_versions.c.id == version_id.value)
                    .values(status="deleted", deleted_at=now)
                )
                event_type = "document.deleted"
            elif kind is LifecycleKind.RESTORE:
                if document["deleted_at"] is None:
                    raise LifecycleConflict("document is not deleted")
                deleted = connection.execute(
                    select(document_versions)
                    .where(
                        document_versions.c.document_id == document_id.value,
                        document_versions.c.status == "deleted",
                    )
                    .order_by(document_versions.c.revision.desc())
                    .limit(1)
                ).mappings().one_or_none()
                if deleted is None:
                    raise LifecycleConflict("restorable version is missing")
                version_id = DocumentVersionId(deleted["id"])
                connection.execute(
                    update(documents)
                    .where(documents.c.id == document_id.value)
                    .values(deleted_at=None, purge_after=None, revision_token=fencing)
                )
                connection.execute(
                    update(document_versions)
                    .where(document_versions.c.id == version_id.value)
                    .values(status="active", deleted_at=None, activated_at=now)
                )
                event_type = "document.restored"
            else:
                if document["deleted_at"] is not None:
                    raise LifecycleConflict("deleted documents cannot be rolled back")
                if target_version_id is None:
                    raise ValueError("rollback requires target_version_id")
                target = connection.execute(
                    select(document_versions).where(
                        document_versions.c.id == target_version_id.value,
                        document_versions.c.tenant_id == context.tenant_id.value,
                        document_versions.c.document_id == document_id.value,
                        document_versions.c.status == "superseded",
                    )
                ).mappings().one_or_none()
                if target is None or active is None:
                    raise LifecycleConflict("rollback target is not a retained superseded version")
                version_id = target_version_id
                metadata["previous_version_id"] = str(active["id"])
                connection.execute(
                    update(document_versions)
                    .where(document_versions.c.id == active["id"])
                    .values(status="superseded", superseded_at=now)
                )
                connection.execute(
                    update(document_versions)
                    .where(document_versions.c.id == target_version_id.value)
                    .values(status="active", activated_at=now, superseded_at=None)
                )
                connection.execute(
                    update(documents)
                    .where(documents.c.id == document_id.value)
                    .values(revision_token=fencing)
                )
                rollback_index_id = self._build_rollback_generation(
                    connection,
                    operation_id=operation_id,
                    tenant_id=context.tenant_id,
                    knowledge_base_id=knowledge_base_id,
                    document_id=document_id,
                    target_version_id=target_version_id,
                    now=now,
                )
                metadata["rollback_index_version_id"] = rollback_index_id
                event_type = "document.rolled_back"
                event_payload.update(
                    document_version_id=str(target_version_id),
                    index_version_id=rollback_index_id,
                )
            self._insert_operation(
                connection,
                operation_id=operation_id,
                context=context,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                version_id=version_id,
                kind=kind,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                reason=reason,
                fencing_token=fencing,
                metadata=metadata,
                now=now,
                status=status,
                progress=progress,
                purge_after=purge_after,
            )
            if event_type is not None:
                self._insert_outbox(
                    connection,
                    context.tenant_id,
                    operation_id,
                    event_type,
                    str(document_id),
                    event_payload,
                    now,
                )
            return LifecycleSubmission(self._load_operation(connection, operation_id))

    @staticmethod
    def _build_rollback_generation(
        connection: Connection,
        *,
        operation_id: OperationId,
        tenant_id: TenantId,
        knowledge_base_id: KnowledgeBaseId,
        document_id: DocumentId,
        target_version_id: DocumentVersionId,
        now: datetime,
    ) -> str:
        route = connection.execute(
            select(index_routes)
            .where(
                index_routes.c.tenant_id == tenant_id.value,
                index_routes.c.knowledge_base_id == knowledge_base_id.value,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if route is None:
            raise LifecycleConflict("rollback requires an active index route")
        active = connection.execute(
            select(index_versions).where(
                index_versions.c.id == route["active_index_version_id"],
                index_versions.c.status == "active",
            )
        ).mappings().one_or_none()
        if active is None:
            raise LifecycleConflict("rollback route does not reference an active generation")
        generation = cast(
            int,
            connection.scalar(
                select(func.max(index_versions.c.generation) + 1).where(
                    index_versions.c.tenant_id == tenant_id.value,
                    index_versions.c.knowledge_base_id == knowledge_base_id.value,
                )
            ),
        )
        new_id = uuid5(_STABLE_NAMESPACE, f"rollback-index:{operation_id}:{generation}")
        inherited = connection.execute(
            select(chunk_embeddings)
            .join(document_chunks, document_chunks.c.id == chunk_embeddings.c.chunk_id)
            .where(
                chunk_embeddings.c.index_version_id == active["id"],
                document_chunks.c.document_id != document_id.value,
            )
        ).mappings()
        target_rows = connection.execute(
            select(chunk_embeddings)
            .join(document_chunks, document_chunks.c.id == chunk_embeddings.c.chunk_id)
            .where(
                document_chunks.c.document_version_id == target_version_id.value,
                chunk_embeddings.c.tenant_id == tenant_id.value,
            )
        ).mappings()
        selected: dict[object, Any] = {}
        for row in (*inherited, *target_rows):
            selected[row["chunk_id"]] = row
        if not selected:
            raise LifecycleConflict("rollback target has no retained embeddings")
        next_token = int(route["fencing_token"]) + 1
        connection.execute(
            insert(index_versions).values(
                id=new_id,
                tenant_id=tenant_id.value,
                knowledge_base_id=knowledge_base_id.value,
                generation=generation,
                status="candidate",
                embedding_model_id=active["embedding_model_id"],
                vector_dimensions=active["vector_dimensions"],
                chunk_count=len(selected),
                fencing_token=next_token,
                created_at=now,
            )
        )
        connection.execute(
            insert(chunk_embeddings),
            [
                {
                    "index_version_id": new_id,
                    "chunk_id": row["chunk_id"],
                    "tenant_id": row["tenant_id"],
                    "knowledge_base_id": row["knowledge_base_id"],
                    "embedding": row["embedding"],
                }
                for row in selected.values()
            ],
        )
        changed = connection.execute(
            update(index_routes)
            .where(
                index_routes.c.tenant_id == tenant_id.value,
                index_routes.c.knowledge_base_id == knowledge_base_id.value,
                index_routes.c.fencing_token == route["fencing_token"],
            )
            .values(
                active_index_version_id=new_id,
                fencing_token=next_token,
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            raise LifecycleConflict("rollback lost its index fencing token")
        connection.execute(
            update(index_versions)
            .where(index_versions.c.id == active["id"])
            .values(status="superseded", superseded_at=now)
        )
        connection.execute(
            update(index_versions)
            .where(index_versions.c.id == new_id)
            .values(status="active", published_at=now)
        )
        return str(new_id)

    def get_operation(
        self, context: AuthorizationContext, operation_id: OperationId
    ) -> LifecycleOperationRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(lifecycle_operations).where(
                    lifecycle_operations.c.id == operation_id.value,
                    lifecycle_operations.c.tenant_id == context.tenant_id.value,
                )
            ).mappings().one_or_none()
        if row is None:
            return None
        CorePolicies.require_knowledge_base(context, KnowledgeBaseId(row["knowledge_base_id"]))
        return self._operation(row)

    def cancel_operation(
        self, context: AuthorizationContext, operation_id: OperationId, now: datetime
    ) -> LifecycleOperationRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(lifecycle_operations)
                .where(
                    lifecycle_operations.c.id == operation_id.value,
                    lifecycle_operations.c.tenant_id == context.tenant_id.value,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise ResourceNotFound("lifecycle operation not found")
            CorePolicies.require_knowledge_base(context, KnowledgeBaseId(row["knowledge_base_id"]))
            current = LifecycleStatus(str(row["status"]))
            if current in TERMINAL_STATUSES:
                return self._operation(row)
            if row["kind"] in {"delete", "restore", "rollback"}:
                raise LifecycleConflict(
                    "committed lifecycle transition requires a compensating command"
                )
            status = (
                LifecycleStatus.CANCEL_REQUESTED
                if current is LifecycleStatus.RUNNING
                else LifecycleStatus.CANCELLED
            )
            connection.execute(
                update(lifecycle_operations)
                .where(lifecycle_operations.c.id == operation_id.value)
                .values(status=status.value, updated_at=now)
            )
            connection.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.operation_id == operation_id.value)
                .values(cancellation_requested=True, updated_at=now)
            )
            if status is LifecycleStatus.CANCELLED:
                connection.execute(
                    update(outbox_messages)
                    .where(
                        outbox_messages.c.operation_id == operation_id.value,
                        outbox_messages.c.status == "pending",
                    )
                    .values(status="cancelled", updated_at=now)
                )
                connection.execute(
                    update(ingestion_jobs)
                    .where(ingestion_jobs.c.operation_id == operation_id.value)
                    .values(status="cancelled", updated_at=now)
                )
            return self._load_operation(connection, operation_id)

    def claim_outbox(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> OutboxMessage | None:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(outbox_messages)
                .where(
                    or_(
                        (
                            (outbox_messages.c.status == "pending")
                            & (outbox_messages.c.available_at <= now)
                        ),
                        (
                            (outbox_messages.c.status == "processing")
                            & (outbox_messages.c.lease_expires_at < now)
                        ),
                    )
                )
                .order_by(outbox_messages.c.available_at, outbox_messages.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).mappings().one_or_none()
            if row is None:
                return None
            operation = connection.execute(
                select(lifecycle_operations)
                .where(lifecycle_operations.c.id == row["operation_id"])
                .with_for_update()
            ).mappings().one()
            if operation["status"] in {"cancel_requested", "cancelled"}:
                connection.execute(
                    update(outbox_messages)
                    .where(outbox_messages.c.id == row["id"])
                    .values(status="cancelled", updated_at=now)
                )
                connection.execute(
                    update(lifecycle_operations)
                    .where(lifecycle_operations.c.id == row["operation_id"])
                    .values(status="cancelled", updated_at=now)
                )
                connection.execute(
                    update(ingestion_jobs)
                    .where(ingestion_jobs.c.operation_id == row["operation_id"])
                    .values(status="cancelled", updated_at=now)
                )
                return None
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            attempts = int(row["attempts"]) + 1
            connection.execute(
                update(outbox_messages)
                .where(outbox_messages.c.id == row["id"])
                .values(
                    status="processing",
                    attempts=attempts,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
            )
            connection.execute(
                update(lifecycle_operations)
                .where(lifecycle_operations.c.id == row["operation_id"])
                .values(status="running", attempts=attempts, progress=0.1, updated_at=now)
            )
            connection.execute(
                update(ingestion_jobs)
                .where(
                    ingestion_jobs.c.operation_id == row["operation_id"],
                    ingestion_jobs.c.status != "succeeded",
                )
                .values(
                    status="running",
                    attempts=attempts,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
            )
            values = dict(row)
            values.update(
                status="processing",
                attempts=attempts,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            return self._message(values)

    def complete_message(self, message: OutboxMessage, now: datetime) -> None:
        with self._engine.begin() as connection:
            changed = connection.execute(
                update(outbox_messages)
                .where(
                    outbox_messages.c.id == message.id.value,
                    outbox_messages.c.status == "processing",
                    outbox_messages.c.lease_owner == message.lease_owner,
                )
                .values(
                    status="published",
                    published_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise LifecycleConflict("Outbox lease was lost before acknowledgement")
            connection.execute(
                update(lifecycle_operations)
                .where(lifecycle_operations.c.id == message.operation_id.value)
                .values(
                    status="succeeded",
                    progress=1.0,
                    next_attempt_at=None,
                    failure_class=None,
                    error_code=None,
                    error_message=None,
                    updated_at=now,
                )
            )
            self._refresh_batches(connection, message.operation_id, now)

    def retry_message(
        self,
        message: OutboxMessage,
        *,
        decision: FailureDecision,
        error_message: str,
        available_at: datetime | None,
        now: datetime,
    ) -> None:
        if decision.classification is FailureClass.CANCELLED:
            message_status = "cancelled"
            operation_status = "cancelled"
        elif available_at is not None:
            message_status = "pending"
            operation_status = "waiting_retry"
        elif decision.retryable:
            message_status = "dead_letter"
            operation_status = "dead_letter"
        else:
            message_status = "dead_letter"
            operation_status = "failed"
        with self._engine.begin() as connection:
            changed = connection.execute(
                update(outbox_messages)
                .where(
                    outbox_messages.c.id == message.id.value,
                    outbox_messages.c.status == "processing",
                    outbox_messages.c.lease_owner == message.lease_owner,
                )
                .values(
                    status=message_status,
                    available_at=available_at or message.available_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error=error_message,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise LifecycleConflict("Outbox lease was lost before failure acknowledgement")
            connection.execute(
                update(lifecycle_operations)
                .where(lifecycle_operations.c.id == message.operation_id.value)
                .values(
                    status=operation_status,
                    next_attempt_at=available_at,
                    failure_class=decision.classification.value,
                    error_code=decision.code,
                    error_message=error_message,
                    updated_at=now,
                )
            )
            connection.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.operation_id == message.operation_id.value)
                .values(
                    status=(
                        "pending"
                        if available_at is not None
                        else "cancelled"
                        if operation_status == "cancelled"
                        else "failed"
                    ),
                    next_attempt_at=available_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    failure_class=decision.classification.value,
                    error_code=decision.code,
                    error_message=error_message,
                    updated_at=now,
                )
            )
            self._refresh_batches(connection, message.operation_id, now)

    def job_for_operation(self, operation_id: OperationId) -> JobId | None:
        with self._engine.connect() as connection:
            value = connection.scalar(
                select(ingestion_jobs.c.id).where(
                    ingestion_jobs.c.operation_id == operation_id.value
                )
            )
        return None if value is None else JobId(value)

    def execute_rebuild(self, operation_id: OperationId, now: datetime) -> None:
        with self._engine.begin() as connection:
            operation = connection.execute(
                select(lifecycle_operations)
                .where(lifecycle_operations.c.id == operation_id.value)
                .with_for_update()
            ).mappings().one()
            tenant_id = operation["tenant_id"]
            knowledge_base_id = operation["knowledge_base_id"]
            route = connection.execute(
                select(index_routes)
                .where(
                    index_routes.c.tenant_id == tenant_id,
                    index_routes.c.knowledge_base_id == knowledge_base_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if route is None:
                raise LifecycleConflict("knowledge base has no active index route")
            operation_metadata = cast(dict[str, object], operation["metadata"])
            completed_index = operation_metadata.get("rebuilt_index_version_id")
            if completed_index is not None and str(route["active_index_version_id"]) == str(
                completed_index
            ):
                return
            expected_token = int(operation["fencing_token"])
            if int(route["fencing_token"]) != expected_token:
                raise LifecycleConflict("rebuild fencing token is stale")
            active = connection.execute(
                select(index_versions).where(
                    index_versions.c.id == route["active_index_version_id"],
                    index_versions.c.status == "active",
                )
            ).mappings().one_or_none()
            if active is None:
                raise LifecycleConflict("routed index generation is not active")
            generation = cast(
                int,
                connection.scalar(
                    select(func.max(index_versions.c.generation) + 1).where(
                        index_versions.c.tenant_id == tenant_id,
                        index_versions.c.knowledge_base_id == knowledge_base_id,
                    )
                ),
            )
            new_id = uuid5(_STABLE_NAMESPACE, f"rebuild:{operation_id}:{generation}")
            connection.execute(
                insert(index_versions).values(
                    id=new_id,
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base_id,
                    generation=generation,
                    status="candidate",
                    embedding_model_id=active["embedding_model_id"],
                    vector_dimensions=active["vector_dimensions"],
                    chunk_count=active["chunk_count"],
                    fencing_token=expected_token + 1,
                    created_at=now,
                )
            )
            rows = connection.execute(
                select(chunk_embeddings).where(
                    chunk_embeddings.c.index_version_id == active["id"]
                )
            ).mappings()
            copies = [
                {
                    "index_version_id": new_id,
                    "chunk_id": row["chunk_id"],
                    "tenant_id": row["tenant_id"],
                    "knowledge_base_id": row["knowledge_base_id"],
                    "embedding": row["embedding"],
                }
                for row in rows
            ]
            if len(copies) != int(active["chunk_count"]) or not copies:
                raise ValueError("rebuild candidate failed chunk-count validation")
            connection.execute(insert(chunk_embeddings), copies)
            changed = connection.execute(
                update(index_routes)
                .where(
                    index_routes.c.tenant_id == tenant_id,
                    index_routes.c.knowledge_base_id == knowledge_base_id,
                    index_routes.c.fencing_token == expected_token,
                )
                .values(
                    active_index_version_id=new_id,
                    fencing_token=expected_token + 1,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise LifecycleConflict("rebuild lost its fencing token")
            connection.execute(
                update(index_versions)
                .where(index_versions.c.id == active["id"])
                .values(status="superseded", superseded_at=now)
            )
            connection.execute(
                update(index_versions)
                .where(index_versions.c.id == new_id)
                .values(status="active", published_at=now)
            )
            connection.execute(
                update(lifecycle_operations)
                .where(lifecycle_operations.c.id == operation_id.value)
                .values(metadata={"rebuilt_index_version_id": str(new_id)}, updated_at=now)
            )

    def purge_document(self, operation_id: OperationId, now: datetime) -> tuple[str, ...]:
        with self._engine.begin() as connection:
            operation = connection.execute(
                select(lifecycle_operations).where(lifecycle_operations.c.id == operation_id.value)
            ).mappings().one()
            operation_metadata = cast(dict[str, object], operation["metadata"])
            prior_keys = operation_metadata.get("purged_object_keys")
            if isinstance(prior_keys, list) and all(isinstance(item, str) for item in prior_keys):
                return tuple(cast(list[str], prior_keys))
            document_id = operation["document_id"]
            document = connection.execute(
                select(documents).where(documents.c.id == document_id).with_for_update()
            ).mappings().one_or_none()
            if (
                document is None
                or document["deleted_at"] is None
                or document["purge_after"] is None
                or document["purge_after"] > now
            ):
                raise LifecycleConflict("document is not eligible for purge")
            versions = list(
                connection.execute(
                    select(document_versions.c.id, document_versions.c.object_key).where(
                        document_versions.c.document_id == document_id
                    )
                ).mappings()
            )
            version_ids = [row["id"] for row in versions]
            chunk_ids = list(
                connection.scalars(
                    select(document_chunks.c.id).where(
                        document_chunks.c.document_version_id.in_(version_ids)
                    )
                )
            )
            if chunk_ids:
                affected_indexes = list(
                    connection.scalars(
                        select(chunk_embeddings.c.index_version_id)
                        .where(chunk_embeddings.c.chunk_id.in_(chunk_ids))
                        .distinct()
                    )
                )
                connection.execute(
                    delete(chunk_embeddings).where(chunk_embeddings.c.chunk_id.in_(chunk_ids))
                )
                for index_id in affected_indexes:
                    count = connection.scalar(
                        select(func.count()).select_from(chunk_embeddings).where(
                            chunk_embeddings.c.index_version_id == index_id
                        )
                    )
                    connection.execute(
                        update(index_versions)
                        .where(index_versions.c.id == index_id)
                        .values(chunk_count=count)
                    )
            if version_ids:
                connection.execute(
                    delete(document_blocks).where(
                        document_blocks.c.document_version_id.in_(version_ids)
                    )
                )
                connection.execute(
                    delete(document_chunks).where(
                        document_chunks.c.document_version_id.in_(version_ids)
                    )
                )
                job_ids = list(
                    connection.scalars(
                        select(ingestion_jobs.c.id).where(
                            ingestion_jobs.c.document_version_id.in_(version_ids)
                        )
                    )
                )
                if job_ids:
                    connection.execute(
                        delete(upload_idempotency_keys).where(
                            upload_idempotency_keys.c.job_id.in_(job_ids)
                        )
                    )
                    connection.execute(
                        delete(ingestion_tasks).where(ingestion_tasks.c.job_id.in_(job_ids))
                    )
                    connection.execute(delete(ingestion_jobs).where(ingestion_jobs.c.id.in_(job_ids)))
                connection.execute(
                    delete(document_versions).where(document_versions.c.id.in_(version_ids))
                )
            connection.execute(delete(documents).where(documents.c.id == document_id))
            keys = tuple(dict.fromkeys(str(row["object_key"]) for row in versions))
            connection.execute(
                update(lifecycle_operations)
                .where(lifecycle_operations.c.id == operation_id.value)
                .values(metadata={"purged_object_keys": list(keys)}, updated_at=now)
            )
            return keys

    def reconcile(self, *, now: datetime, dry_run: bool) -> ReconciliationReport:
        findings: list[ReconciliationFinding] = []
        with self._engine.begin() as connection:
            expired = list(
                connection.execute(
                    select(outbox_messages).where(
                        outbox_messages.c.status == "processing",
                        outbox_messages.c.lease_expires_at < now,
                    )
                ).mappings()
            )
            for row in expired:
                repaired = not dry_run
                findings.append(
                    ReconciliationFinding(
                        "expired_outbox_lease",
                        str(row["message_id"]),
                        True,
                        repaired,
                        "return message to pending for at-least-once delivery",
                    )
                )
                if repaired:
                    connection.execute(
                        update(outbox_messages)
                        .where(outbox_messages.c.id == row["id"])
                        .values(
                            status="pending",
                            lease_owner=None,
                            lease_expires_at=None,
                            available_at=now,
                            updated_at=now,
                        )
                    )
            stale_before = now - timedelta(minutes=15)
            stale_operations = list(
                connection.execute(
                    select(lifecycle_operations).where(
                        lifecycle_operations.c.status.in_(
                            ("pending", "running", "waiting_retry", "cancel_requested")
                        ),
                        lifecycle_operations.c.updated_at < stale_before,
                    )
                ).mappings()
            )
            for operation in stale_operations:
                findings.append(
                    ReconciliationFinding(
                        "stale_lifecycle_operation",
                        str(operation["id"]),
                        False,
                        False,
                        f"status={operation['status']}; operator review required",
                    )
                )
                outbox_count = connection.scalar(
                    select(func.count()).select_from(outbox_messages).where(
                        outbox_messages.c.operation_id == operation["id"]
                    )
                )
                if not outbox_count:
                    findings.append(
                        ReconciliationFinding(
                            "missing_outbox",
                            str(operation["id"]),
                            False,
                            False,
                            "authoritative operation has no delivery record",
                        )
                    )
                document_missing = operation["document_id"] is not None and not connection.scalar(
                    select(func.count()).select_from(documents).where(
                        documents.c.id == operation["document_id"],
                        documents.c.tenant_id == operation["tenant_id"],
                    )
                )
                version_missing = (
                    operation["document_version_id"] is not None
                    and not connection.scalar(
                        select(func.count()).select_from(document_versions).where(
                            document_versions.c.id == operation["document_version_id"],
                            document_versions.c.tenant_id == operation["tenant_id"],
                        )
                    )
                )
                if document_missing or version_missing:
                    findings.append(
                        ReconciliationFinding(
                            "missing_authoritative_state",
                            str(operation["id"]),
                            False,
                            False,
                            "document or version referenced by the operation is missing",
                        )
                    )
            due_outbox = list(
                connection.execute(
                    select(outbox_messages.c.message_id, outbox_messages.c.status).where(
                        outbox_messages.c.status == "pending",
                        outbox_messages.c.available_at <= now,
                    )
                ).mappings()
            )
            findings.extend(
                ReconciliationFinding(
                    "outbox_due",
                    str(row["message_id"]),
                    True,
                    False,
                    "message is ready for Worker delivery",
                )
                for row in due_outbox
            )
            due_documents = list(
                connection.execute(
                    select(documents).where(
                        documents.c.deleted_at.is_not(None),
                        documents.c.purge_after <= now,
                    )
                ).mappings()
            )
            for document in due_documents:
                purge_key = f"purge:{document['id']}:{document['purge_after'].isoformat()}"
                operation_id = _operation_id(TenantId(document["tenant_id"]), purge_key)
                exists = connection.scalar(
                    select(func.count()).select_from(lifecycle_operations).where(
                        lifecycle_operations.c.id == operation_id.value
                    )
                )
                repaired = not dry_run and not bool(exists)
                findings.append(
                    ReconciliationFinding(
                        "expired_document_tombstone",
                        str(document["id"]),
                        True,
                        repaired,
                        "schedule idempotent physical purge",
                    )
                )
                if repaired:
                    context = AuthorizationContext.from_principal(
                        TrustedPrincipal(
                            TenantId(document["tenant_id"]),
                            ActorId(uuid5(_STABLE_NAMESPACE, "system:maintainer")),
                            frozenset({"admin"}),
                        )
                    )
                    self._insert_operation(
                        connection,
                        operation_id=operation_id,
                        context=context,
                        knowledge_base_id=KnowledgeBaseId(document["knowledge_base_id"]),
                        document_id=DocumentId(document["id"]),
                        version_id=None,
                        kind=LifecycleKind.PURGE,
                        idempotency_key=purge_key,
                        fingerprint=_fingerprint(purge_key),
                        reason="retention period expired",
                        fencing_token=int(document["revision_token"]),
                        metadata={},
                        now=now,
                    )
                    self._insert_outbox(
                        connection,
                        TenantId(document["tenant_id"]),
                        operation_id,
                        "document.purge_requested",
                        str(document["id"]),
                        {
                            "tenant_id": str(document["tenant_id"]),
                            "document_id": str(document["id"]),
                        },
                        now,
                    )
            orphan_candidates = list(
                connection.execute(
                    select(index_versions.c.id).where(
                        index_versions.c.status == "candidate",
                        index_versions.c.created_at < now - timedelta(minutes=15),
                    )
                ).scalars()
            )
            findings.extend(
                ReconciliationFinding(
                    "stale_candidate_generation",
                    str(value),
                    False,
                    False,
                    "operator review required; active route is left unchanged",
                )
                for value in orphan_candidates
            )
        return ReconciliationReport(dry_run, tuple(findings), now)

    def reconciliation_inventory(self) -> ReconciliationInventory:
        with self._engine.connect() as connection:
            object_keys = frozenset(
                (TenantId(row["tenant_id"]), str(row["object_key"]))
                for row in connection.execute(
                    select(document_versions.c.tenant_id, document_versions.c.object_key)
                ).mappings()
            )
            projected = tuple(
                ProjectionVersionRecord(
                    TenantId(row["tenant_id"]),
                    DocumentVersionId(row["document_version_id"]),
                    int(row["chunk_count"]),
                )
                for row in connection.execute(
                    select(
                        document_versions.c.tenant_id,
                        document_versions.c.id.label("document_version_id"),
                        func.count(document_chunks.c.id).label("chunk_count"),
                    )
                    .join(
                        document_chunks,
                        document_chunks.c.document_version_id == document_versions.c.id,
                    )
                    .group_by(document_versions.c.tenant_id, document_versions.c.id)
                ).mappings()
            )
        return ReconciliationInventory(object_keys, projected)

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
    ) -> LifecycleBatchRecord:
        if not operation_ids or len(operation_ids) > 1000 or len(set(operation_ids)) != len(
            operation_ids
        ):
            raise ValueError("batch requires 1..1000 unique operations")
        CorePolicies.require_knowledge_base(context, knowledge_base_id)
        batch_id = BatchId(uuid5(_STABLE_NAMESPACE, f"batch:{context.tenant_id}:{idempotency_key}"))
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(lifecycle_batches).where(
                    lifecycle_batches.c.tenant_id == context.tenant_id.value,
                    lifecycle_batches.c.idempotency_key == idempotency_key,
                )
            ).mappings().one_or_none()
            if existing is not None:
                if (
                    existing["requested_by"] != context.actor_id.value
                    or existing["knowledge_base_id"] != knowledge_base_id.value
                    or existing["kind"] != kind.value
                    or int(existing["concurrency"]) != concurrency
                    or tuple(existing["operation_ids"])
                    != tuple(item.value for item in operation_ids)
                ):
                    raise IdempotencyConflict("batch idempotency key is bound to different input")
                return self._batch(existing)
            count = connection.scalar(
                select(func.count()).select_from(lifecycle_operations).where(
                    lifecycle_operations.c.id.in_([item.value for item in operation_ids]),
                    lifecycle_operations.c.tenant_id == context.tenant_id.value,
                    lifecycle_operations.c.knowledge_base_id == knowledge_base_id.value,
                    lifecycle_operations.c.requested_by == context.actor_id.value,
                    lifecycle_operations.c.kind == kind.value,
                )
            )
            if count != len(operation_ids):
                raise LifecycleConflict("batch child crosses actor, kind, or knowledge-base scope")
            connection.execute(
                insert(lifecycle_batches).values(
                    id=batch_id.value,
                    tenant_id=context.tenant_id.value,
                    knowledge_base_id=knowledge_base_id.value,
                    requested_by=context.actor_id.value,
                    kind=kind.value,
                    idempotency_key=idempotency_key,
                    concurrency=concurrency,
                    operation_ids=[item.value for item in operation_ids],
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
            self._refresh_batch(connection, batch_id.value, now)
            return self._batch(
                connection.execute(
                    select(lifecycle_batches).where(lifecycle_batches.c.id == batch_id.value)
                ).mappings().one()
            )

    def get_batch(
        self, context: AuthorizationContext, batch_id: BatchId
    ) -> LifecycleBatchRecord | None:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(lifecycle_batches).where(
                    lifecycle_batches.c.id == batch_id.value,
                    lifecycle_batches.c.tenant_id == context.tenant_id.value,
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            CorePolicies.require_knowledge_base(context, KnowledgeBaseId(row["knowledge_base_id"]))
            if row["requested_by"] != context.actor_id.value:
                raise AccessDenied("only the batch requester may read the batch")
            self._refresh_batch(connection, batch_id.value, cast(datetime, row["updated_at"]))
            row = connection.execute(
                select(lifecycle_batches).where(lifecycle_batches.c.id == batch_id.value)
            ).mappings().one()
            return self._batch(row)

    def _duplicate(
        self,
        connection: Connection,
        context: AuthorizationContext,
        idempotency_key: str,
        fingerprint: str,
    ) -> LifecycleSubmission | None:
        row = connection.execute(
            select(lifecycle_operations).where(
                lifecycle_operations.c.tenant_id == context.tenant_id.value,
                lifecycle_operations.c.idempotency_key == idempotency_key,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["request_sha256"] != fingerprint:
            raise IdempotencyConflict("idempotency key is bound to different lifecycle input")
        job = connection.execute(
            select(ingestion_jobs).where(ingestion_jobs.c.operation_id == row["id"])
        ).mappings().one_or_none()
        return LifecycleSubmission(
            self._operation(row),
            None if job is None else self._job(job),
            True,
        )

    @staticmethod
    def _insert_operation(
        connection: Connection,
        *,
        operation_id: OperationId,
        context: AuthorizationContext,
        knowledge_base_id: KnowledgeBaseId,
        document_id: DocumentId | None,
        version_id: DocumentVersionId | None,
        kind: LifecycleKind,
        idempotency_key: str,
        fingerprint: str,
        reason: str,
        fencing_token: int,
        metadata: dict[str, object],
        now: datetime,
        status: LifecycleStatus = LifecycleStatus.PENDING,
        progress: float = 0.0,
        purge_after: datetime | None = None,
    ) -> None:
        connection.execute(
            insert(lifecycle_operations).values(
                id=operation_id.value,
                tenant_id=context.tenant_id.value,
                knowledge_base_id=knowledge_base_id.value,
                document_id=None if document_id is None else document_id.value,
                document_version_id=None if version_id is None else version_id.value,
                requested_by=context.actor_id.value,
                kind=kind.value,
                idempotency_key=idempotency_key,
                request_sha256=fingerprint,
                reason=reason,
                status=status.value,
                progress=progress,
                fencing_token=fencing_token,
                purge_after=purge_after,
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _insert_ingestion(
        connection: Connection,
        *,
        context: AuthorizationContext,
        knowledge_base_id: KnowledgeBaseId,
        document_id: DocumentId,
        version_id: DocumentVersionId,
        operation_id: OperationId,
        job_id: JobId,
        idempotency_key: str,
        fingerprint: str,
        now: datetime,
    ) -> None:
        connection.execute(
            insert(ingestion_jobs).values(
                id=job_id.value,
                tenant_id=context.tenant_id.value,
                knowledge_base_id=knowledge_base_id.value,
                document_id=document_id.value,
                document_version_id=version_id.value,
                requested_by=context.actor_id.value,
                idempotency_key=idempotency_key,
                request_sha256=fingerprint,
                trace_id=uuid5(_STABLE_NAMESPACE, f"trace:{operation_id}"),
                status="pending",
                progress=0.0,
                operation_id=operation_id.value,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(ingestion_tasks),
            [
                {
                    "id": uuid5(_STABLE_NAMESPACE, f"task:{job_id}:{task}"),
                    "tenant_id": context.tenant_id.value,
                    "job_id": job_id.value,
                    "task": task,
                    "status": "pending",
                    "progress": 0.0,
                    "created_at": now,
                    "updated_at": now,
                }
                for task in _TASKS
            ],
        )

    @staticmethod
    def _insert_outbox(
        connection: Connection,
        tenant_id: TenantId,
        operation_id: OperationId,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        value = uuid5(_STABLE_NAMESPACE, f"outbox:{operation_id}:{event_type}")
        connection.execute(
            insert(outbox_messages).values(
                id=value,
                message_id=f"lifecycle:{operation_id}:{event_type}:v1",
                tenant_id=tenant_id.value,
                operation_id=operation_id.value,
                event_type=event_type,
                aggregate_id=aggregate_id,
                payload=payload,
                status="pending",
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    def _document_for_write(
        self, connection: Connection, context: AuthorizationContext, document_id: DocumentId
    ) -> Any:
        row = connection.execute(
            select(documents)
            .where(
                documents.c.id == document_id.value,
                documents.c.tenant_id == context.tenant_id.value,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("document not found")
        self._knowledge_base_for_write(
            connection, context, KnowledgeBaseId(row["knowledge_base_id"])
        )
        return row

    @staticmethod
    def _knowledge_base_for_write(
        connection: Connection,
        context: AuthorizationContext,
        knowledge_base_id: KnowledgeBaseId,
    ) -> Any:
        CorePolicies.require_knowledge_base(context, knowledge_base_id)
        row = connection.execute(
            select(knowledge_bases).where(
                knowledge_bases.c.id == knowledge_base_id.value,
                knowledge_bases.c.tenant_id == context.tenant_id.value,
                knowledge_bases.c.status == "active",
            )
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("knowledge base not found")
        if (
            row["visibility"] == "private"
            and row["owner_id"] != context.actor_id.value
            and "admin" not in context.roles
        ):
            raise AccessDenied("private knowledge base is not writable by this actor")
        return row

    def _load_operation(
        self, connection: Connection, operation_id: OperationId
    ) -> LifecycleOperationRecord:
        return self._operation(
            connection.execute(
                select(lifecycle_operations).where(lifecycle_operations.c.id == operation_id.value)
            ).mappings().one()
        )

    @staticmethod
    def _operation(row: Any) -> LifecycleOperationRecord:
        return LifecycleOperationRecord(
            OperationId(row["id"]),
            TenantId(row["tenant_id"]),
            KnowledgeBaseId(row["knowledge_base_id"]),
            None if row["document_id"] is None else DocumentId(row["document_id"]),
            (
                None
                if row["document_version_id"] is None
                else DocumentVersionId(row["document_version_id"])
            ),
            ActorId(row["requested_by"]),
            LifecycleKind(str(row["kind"])),
            str(row["idempotency_key"]),
            str(row["reason"]),
            LifecycleStatus(str(row["status"])),
            float(row["progress"]),
            int(row["attempts"]),
            int(row["fencing_token"]),
            cast(datetime, row["created_at"]),
            cast(datetime, row["updated_at"]),
            cast(datetime | None, row["next_attempt_at"]),
            cast(datetime | None, row["purge_after"]),
            None if row["failure_class"] is None else FailureClass(str(row["failure_class"])),
            cast(str | None, row["error_code"]),
            cast(str | None, row["error_message"]),
            cast(dict[str, object], row["metadata"]),
        )

    @staticmethod
    def _message(row: Any) -> OutboxMessage:
        return OutboxMessage(
            OutboxMessageId(row["id"]),
            str(row["message_id"]),
            TenantId(row["tenant_id"]),
            OperationId(row["operation_id"]),
            str(row["event_type"]),
            str(row["aggregate_id"]),
            cast(dict[str, object], row["payload"]),
            OutboxStatus(str(row["status"])),
            int(row["attempts"]),
            int(row["max_attempts"]),
            cast(datetime, row["available_at"]),
            cast(str | None, row["lease_owner"]),
            cast(datetime | None, row["lease_expires_at"]),
        )

    @staticmethod
    def _job(row: Any) -> IngestionJobRecord:
        return IngestionJobRecord(
            JobId(row["id"]),
            TenantId(row["tenant_id"]),
            KnowledgeBaseId(row["knowledge_base_id"]),
            DocumentId(row["document_id"]),
            DocumentVersionId(row["document_version_id"]),
            ActorId(row["requested_by"]),
            str(row["idempotency_key"]),
            TraceId(row["trace_id"]),
            WorkStatus(str(row["status"])),
            float(row["progress"]),
            cast(datetime, row["created_at"]),
            cast(datetime, row["updated_at"]),
            cast(str | None, row["error_code"]),
            cast(str | None, row["error_message"]),
            OperationId(row["operation_id"]),
            int(row["attempts"]),
            int(row["max_attempts"]),
            cast(datetime | None, row["next_attempt_at"]),
            bool(row["cancellation_requested"]),
            cast(str | None, row["failure_class"]),
        )

    @staticmethod
    def _batch(row: Any) -> LifecycleBatchRecord:
        return LifecycleBatchRecord(
            BatchId(row["id"]),
            TenantId(row["tenant_id"]),
            KnowledgeBaseId(row["knowledge_base_id"]),
            ActorId(row["requested_by"]),
            LifecycleKind(str(row["kind"])),
            str(row["idempotency_key"]),
            int(row["concurrency"]),
            tuple(OperationId(value) for value in row["operation_ids"]),
            str(row["status"]),
            int(row["succeeded"]),
            int(row["failed"]),
            int(row["cancelled"]),
            cast(datetime, row["created_at"]),
            cast(datetime, row["updated_at"]),
        )

    def _refresh_batches(
        self, connection: Connection, operation_id: OperationId, now: datetime
    ) -> None:
        ids = connection.scalars(
            select(lifecycle_batches.c.id).where(
                lifecycle_batches.c.operation_ids.any(operation_id.value)
            )
        )
        for batch_id in ids:
            self._refresh_batch(connection, batch_id, now)

    @staticmethod
    def _refresh_batch(connection: Connection, batch_id: Any, now: datetime) -> None:
        batch = connection.execute(
            select(lifecycle_batches).where(lifecycle_batches.c.id == batch_id)
        ).mappings().one()
        statuses = list(
            connection.scalars(
                select(lifecycle_operations.c.status).where(
                    lifecycle_operations.c.id.in_(batch["operation_ids"])
                )
            )
        )
        succeeded = statuses.count("succeeded")
        cancelled = statuses.count("cancelled")
        failed = statuses.count("failed") + statuses.count("dead_letter")
        terminal = succeeded + failed + cancelled
        if terminal == len(statuses):
            if cancelled == len(statuses):
                status = "cancelled"
            elif succeeded == len(statuses):
                status = "succeeded"
            elif succeeded:
                status = "partial_success"
            else:
                status = "failed"
        elif any(item in {"running", "waiting_retry"} for item in statuses):
            status = "running"
        else:
            status = "pending"
        connection.execute(
            update(lifecycle_batches)
            .where(lifecycle_batches.c.id == batch_id)
            .values(
                status=status,
                succeeded=succeeded,
                failed=failed,
                cancelled=cancelled,
                updated_at=now,
            )
        )


def _operation_id(tenant_id: TenantId, idempotency_key: str) -> OperationId:
    return OperationId(uuid5(_STABLE_NAMESPACE, f"operation:{tenant_id}:{idempotency_key}"))


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
