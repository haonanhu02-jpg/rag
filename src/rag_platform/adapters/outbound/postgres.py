"""PostgreSQL authority and pgvector adapters with mandatory tenant predicates."""

from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.entities import KnowledgeBase, VersionStatus, WorkStatus
from rag_platform.domain.identifiers import (
    ActorId,
    ChunkId,
    DocumentId,
    DocumentVersionId,
    IndexVersionId,
    JobId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)
from rag_platform.domain.policies import CorePolicies, ResourceNotFound
from rag_platform.modules.knowledge.contracts import (
    CompiledDocument,
    IdempotencyConflict,
    IngestionJobRecord,
    IngestionSource,
    KnowledgeBaseRecord,
    RetrievalTraceRecord,
    SearchHit,
    StagedGeneration,
    UploadSubmission,
)

metadata = MetaData()
_STABLE_NAMESPACE = UUID("c4e188ff-9b5e-52ba-94e7-0ef263d4c715")

tenants = Table(
    "tenants",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
knowledge_bases = Table(
    "knowledge_bases",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("owner_id", PGUUID(as_uuid=True), nullable=False),
    Column("name", String(200), nullable=False),
    Column("description", String(4000), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id_id"),
)
documents = Table(
    "documents",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", PGUUID(as_uuid=True), nullable=False),
    Column("external_key", String(500), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
document_versions = Table(
    "document_versions",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("document_id", PGUUID(as_uuid=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("file_name", String(500), nullable=False),
    Column("media_type", String(100), nullable=False),
    Column("object_key", String(1000), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=True),
    Column("chunk_method", String(32), nullable=False, server_default="general"),
    Column("parser_name", String(100), nullable=True),
    Column("parser_version", String(50), nullable=True),
    Column("parse_schema_version", Integer, nullable=True),
    Column("parse_warnings", JSONB, nullable=False, server_default="[]"),
    Column("page_count", Integer, nullable=True),
    Column("normalized_sha256", String(64), nullable=True),
)
ingestion_jobs = Table(
    "ingestion_jobs",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", PGUUID(as_uuid=True), nullable=False),
    Column("document_id", PGUUID(as_uuid=True), nullable=False),
    Column("document_version_id", PGUUID(as_uuid=True), nullable=False),
    Column("requested_by", PGUUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(300), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("trace_id", PGUUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("progress", Float, nullable=False),
    Column("error_code", String(100), nullable=True),
    Column("error_message", String(1000), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
upload_idempotency_keys = Table(
    "upload_idempotency_keys",
    metadata,
    Column("tenant_id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(300), primary_key=True),
    Column("request_sha256", String(64), nullable=False),
    Column("job_id", PGUUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
document_blocks = Table(
    "document_blocks",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("document_version_id", PGUUID(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("kind", String(50), nullable=False),
    Column("text", Text, nullable=False),
    Column("start_character", Integer, nullable=False),
    Column("end_character", Integer, nullable=False),
    Column("page_number", Integer, nullable=True),
    Column("bounding_box", JSONB, nullable=True),
    Column("heading_path", ARRAY(Text), nullable=False, server_default="{}"),
    Column("table_metadata", JSONB, nullable=True),
    Column("media_reference", JSONB, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("parser_name", String(100), nullable=False, server_default="plain-text"),
    Column("parser_version", String(50), nullable=False, server_default="1"),
    Column("warnings", JSONB, nullable=False, server_default="[]"),
)
document_chunks = Table(
    "document_chunks",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", PGUUID(as_uuid=True), nullable=False),
    Column("document_id", PGUUID(as_uuid=True), nullable=False),
    Column("document_version_id", PGUUID(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("source", JSONB, nullable=False),
)
index_versions = Table(
    "index_versions",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", PGUUID(as_uuid=True), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("embedding_model_id", String(200), nullable=False),
    Column("vector_dimensions", Integer, nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
)
chunk_embeddings = Table(
    "chunk_embeddings",
    metadata,
    Column("index_version_id", PGUUID(as_uuid=True), primary_key=True),
    Column("chunk_id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", PGUUID(as_uuid=True), nullable=False),
    Column("embedding", VECTOR(8), nullable=False),
)
retrieval_traces = Table(
    "retrieval_traces",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
    Column("knowledge_base_ids", ARRAY(PGUUID(as_uuid=True)), nullable=False),
    Column("query_sha256", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("candidate_count", Integer, nullable=False),
    Column("selected_chunk_ids", ARRAY(PGUUID(as_uuid=True)), nullable=False),
    Column("authorization_applied", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("canonical_query_sha256", String(64), nullable=False, server_default=""),
    Column("query_variant_sha256", ARRAY(Text), nullable=False, server_default="{}"),
    Column("events", JSONB, nullable=False, server_default="[]"),
    Column("candidate_traces", JSONB, nullable=False, server_default="[]"),
    Column("fallback_steps", JSONB, nullable=False, server_default="[]"),
    Column("filter_summary", ARRAY(Text), nullable=False, server_default="{}"),
    Column("provider_ids", ARRAY(Text), nullable=False, server_default="{}"),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(100), nullable=True),
    Column("request_id", String(200), nullable=True),
    Column("index_version_ids", ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default="{}"),
)


def create_postgres_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


class SqlAlchemyTransaction(AbstractContextManager[None]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._transaction = connection.begin()

    def __enter__(self) -> None:
        return None

    def commit(self) -> None:
        self._transaction.commit()

    def rollback(self) -> None:
        self._transaction.rollback()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.commit() if exc_type is None else self.rollback()
        finally:
            self._connection.close()


class SqlAlchemyTransactionManager:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def transaction(self) -> SqlAlchemyTransaction:
        return SqlAlchemyTransaction(self._engine.connect())


class PostgresKnowledgeBaseRepository:
    """R1 compatibility adapter backed by the expanded R2 table."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, knowledge_base: KnowledgeBase) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(knowledge_bases).values(
                    id=knowledge_base.id.value,
                    tenant_id=knowledge_base.tenant_id.value,
                    owner_id=knowledge_base.tenant_id.value,
                    name=knowledge_base.name,
                    description="",
                    visibility="private",
                    status="active",
                    created_at=knowledge_base.created_at,
                    updated_at=knowledge_base.created_at,
                )
            )

    def get(self, tenant_id: TenantId, knowledge_base_id: KnowledgeBaseId) -> KnowledgeBase | None:
        statement = select(knowledge_bases).where(
            knowledge_bases.c.id == knowledge_base_id.value,
            knowledge_bases.c.tenant_id == tenant_id.value,
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        return KnowledgeBase(
            KnowledgeBaseId(row["id"]),
            TenantId(row["tenant_id"]),
            str(row["name"]),
            cast(datetime, row["created_at"]),
        )


class PostgresKnowledgeRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_knowledge_base(self, value: KnowledgeBaseRecord) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                pg_insert(tenants)
                .values(
                    id=value.tenant_id.value,
                    name=f"tenant-{value.tenant_id}",
                    status="active",
                    created_at=value.created_at,
                )
                .on_conflict_do_nothing(index_elements=[tenants.c.id])
            )
            connection.execute(
                insert(knowledge_bases).values(
                    id=value.id.value,
                    tenant_id=value.tenant_id.value,
                    owner_id=value.owner_id.value,
                    name=value.name,
                    description=value.description,
                    visibility=value.visibility,
                    status=value.status,
                    created_at=value.created_at,
                    updated_at=value.updated_at,
                )
            )

    def get_knowledge_base(
        self, context: AuthorizationContext, knowledge_base_id: KnowledgeBaseId
    ) -> KnowledgeBaseRecord | None:
        CorePolicies.require_knowledge_base(context, knowledge_base_id)
        statement = select(knowledge_bases).where(
            knowledge_bases.c.id == knowledge_base_id.value,
            knowledge_bases.c.tenant_id == context.tenant_id.value,
            knowledge_bases.c.status == "active",
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        if (
            row["visibility"] == "private"
            and row["owner_id"] != context.actor_id.value
            and "admin" not in context.roles
        ):
            return None
        return self._knowledge_base(row)

    def register_upload(
        self,
        *,
        context: AuthorizationContext,
        knowledge_base_id: KnowledgeBaseId,
        file_name: str,
        media_type: str,
        object_key: str,
        source_sha256: str,
        size_bytes: int,
        idempotency_key: str,
        chunk_method: str,
        now: datetime,
    ) -> UploadSubmission:
        request_sha256 = hashlib.sha256(
            f"{knowledge_base_id}\x1f{file_name}\x1f{media_type}\x1f{source_sha256}\x1f{chunk_method}".encode()
        ).hexdigest()
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    select(ingestion_jobs, upload_idempotency_keys.c.request_sha256)
                    .join(
                        upload_idempotency_keys,
                        upload_idempotency_keys.c.job_id == ingestion_jobs.c.id,
                    )
                    .where(
                        upload_idempotency_keys.c.tenant_id == context.tenant_id.value,
                        upload_idempotency_keys.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise IdempotencyConflict("idempotency key is bound to different input")
                return UploadSubmission(self._job(existing), True)

            document_id = DocumentId(
                uuid5(
                    _STABLE_NAMESPACE,
                    f"document:{context.tenant_id}:{knowledge_base_id}:{file_name.casefold()}",
                )
            )
            document = (
                connection.execute(
                    select(documents).where(
                        documents.c.id == document_id.value,
                        documents.c.tenant_id == context.tenant_id.value,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document is None:
                connection.execute(
                    insert(documents).values(
                        id=document_id.value,
                        tenant_id=context.tenant_id.value,
                        knowledge_base_id=knowledge_base_id.value,
                        external_key=file_name,
                        created_at=now,
                    )
                )
            existing_version = (
                connection.execute(
                    select(document_versions).where(
                        document_versions.c.tenant_id == context.tenant_id.value,
                        document_versions.c.document_id == document_id.value,
                        document_versions.c.source_sha256 == source_sha256,
                        document_versions.c.chunk_method == chunk_method,
                    )
                )
                .mappings()
                .one_or_none()
            )
            version_id = DocumentVersionId(
                uuid5(
                    _STABLE_NAMESPACE,
                    f"version:{document_id}:{source_sha256}:{chunk_method}",
                )
            )
            if existing_version is None:
                revision = cast(
                    int,
                    connection.scalar(
                        select(func.coalesce(func.max(document_versions.c.revision), 0) + 1).where(
                            document_versions.c.tenant_id == context.tenant_id.value,
                            document_versions.c.document_id == document_id.value,
                        )
                    ),
                )
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
            else:
                version_id = DocumentVersionId(existing_version["id"])
                prior_job = (
                    connection.execute(
                        select(ingestion_jobs).where(
                            ingestion_jobs.c.tenant_id == context.tenant_id.value,
                            ingestion_jobs.c.document_version_id == version_id.value,
                        )
                    )
                    .mappings()
                    .first()
                )
                if prior_job is not None:
                    connection.execute(
                        insert(upload_idempotency_keys).values(
                            tenant_id=context.tenant_id.value,
                            idempotency_key=idempotency_key,
                            request_sha256=request_sha256,
                            job_id=prior_job["id"],
                            created_at=now,
                        )
                    )
                    return UploadSubmission(self._job(prior_job), True)
            job_id = JobId(uuid5(_STABLE_NAMESPACE, f"job:{context.tenant_id}:{idempotency_key}"))
            trace_id = TraceId(uuid5(_STABLE_NAMESPACE, f"upload-trace:{job_id}"))
            connection.execute(
                insert(ingestion_jobs).values(
                    id=job_id.value,
                    tenant_id=context.tenant_id.value,
                    knowledge_base_id=knowledge_base_id.value,
                    document_id=document_id.value,
                    document_version_id=version_id.value,
                    requested_by=context.actor_id.value,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    trace_id=trace_id.value,
                    status="pending",
                    progress=0.0,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(upload_idempotency_keys).values(
                    tenant_id=context.tenant_id.value,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    job_id=job_id.value,
                    created_at=now,
                )
            )
            row = (
                connection.execute(
                    select(ingestion_jobs).where(ingestion_jobs.c.id == job_id.value)
                )
                .mappings()
                .one()
            )
            return UploadSubmission(self._job(row), False)

    def get_job(self, context: AuthorizationContext, job_id: JobId) -> IngestionJobRecord | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(ingestion_jobs).where(
                        ingestion_jobs.c.id == job_id.value,
                        ingestion_jobs.c.tenant_id == context.tenant_id.value,
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else self._job(row)

    def next_pending_job(self) -> JobId | None:
        with self._engine.connect() as connection:
            value = connection.scalar(
                select(ingestion_jobs.c.id)
                .where(ingestion_jobs.c.status == "pending")
                .order_by(ingestion_jobs.c.created_at)
                .limit(1)
            )
        return None if value is None else JobId(value)

    def begin_ingestion(self, job_id: JobId, now: datetime) -> IngestionSource:
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    select(
                        ingestion_jobs,
                        document_versions.c.file_name,
                        document_versions.c.media_type,
                        document_versions.c.object_key,
                        document_versions.c.source_sha256,
                        document_versions.c.chunk_method,
                    )
                    .join(
                        document_versions,
                        document_versions.c.id == ingestion_jobs.c.document_version_id,
                    )
                    .where(ingestion_jobs.c.id == job_id.value)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ResourceNotFound("ingestion job not found")
            if row["status"] == "succeeded":
                return self._source(row)
            connection.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.id == job_id.value)
                .values(status="running", progress=0.1, updated_at=now)
            )
            values = dict(row)
            values.update(status="running", progress=0.1, updated_at=now)
            return self._source(values)

    def stage_generation(
        self,
        source: IngestionSource,
        document: CompiledDocument,
        vectors: tuple[tuple[float, ...], ...],
        embedding_model_id: str,
        now: datetime,
    ) -> StagedGeneration:
        if len(document.chunks) != len(vectors) or not vectors:
            raise ValueError("each chunk requires one embedding")
        dimensions = len(vectors[0])
        if dimensions != 8 or any(len(vector) != dimensions for vector in vectors):
            raise ValueError("R2 pgvector profile requires 8 dimensions")
        with self._engine.begin() as connection:
            parsed = document.parsed_document
            connection.execute(
                update(document_versions)
                .where(document_versions.c.id == source.job.document_version_id.value)
                .values(
                    parser_name=None if parsed is None else parsed.parser_name,
                    parser_version=None if parsed is None else parsed.parser_version,
                    parse_schema_version=None if parsed is None else parsed.schema_version,
                    parse_warnings=(
                        []
                        if parsed is None
                        else [
                            {
                                "code": warning.code,
                                "message": warning.message,
                                "page_number": warning.page_number,
                            }
                            for warning in parsed.warnings
                        ]
                    ),
                    page_count=None if parsed is None else parsed.page_count,
                    normalized_sha256=document.normalized_sha256,
                )
            )
            active = (
                connection.execute(
                    select(index_versions).where(
                        index_versions.c.tenant_id == source.job.tenant_id.value,
                        index_versions.c.knowledge_base_id == source.job.knowledge_base_id.value,
                        index_versions.c.status == "active",
                    )
                )
                .mappings()
                .one_or_none()
            )
            generation = 1 if active is None else int(active["generation"]) + 1
            index_version_id = IndexVersionId(uuid4())
            connection.execute(
                insert(index_versions).values(
                    id=index_version_id.value,
                    tenant_id=source.job.tenant_id.value,
                    knowledge_base_id=source.job.knowledge_base_id.value,
                    generation=generation,
                    status="candidate",
                    embedding_model_id=embedding_model_id,
                    vector_dimensions=dimensions,
                    chunk_count=len(document.chunks),
                    created_at=now,
                )
            )
            connection.execute(
                delete(document_blocks).where(
                    document_blocks.c.document_version_id == source.job.document_version_id.value
                )
            )
            connection.execute(
                delete(document_chunks).where(
                    document_chunks.c.document_version_id == source.job.document_version_id.value
                )
            )
            if document.blocks:
                connection.execute(
                    insert(document_blocks),
                    [
                        {
                            "id": block.id.value,
                            "tenant_id": source.job.tenant_id.value,
                            "document_version_id": source.job.document_version_id.value,
                            "ordinal": block.ordinal,
                            "kind": block.kind,
                            "text": block.text,
                            "start_character": block.start_character,
                            "end_character": block.end_character,
                            "page_number": block.page_number,
                            "bounding_box": (
                                None
                                if block.bounding_box is None
                                else {
                                    "x0": block.bounding_box.x0,
                                    "y0": block.bounding_box.y0,
                                    "x1": block.bounding_box.x1,
                                    "y1": block.bounding_box.y1,
                                    "coordinate_space": block.bounding_box.coordinate_space,
                                }
                            ),
                            "heading_path": list(block.heading_path),
                            "table_metadata": (
                                None
                                if block.table is None
                                else {
                                    "rows": block.table.rows,
                                    "columns": block.table.columns,
                                    "has_header": block.table.has_header,
                                }
                            ),
                            "media_reference": (
                                None
                                if block.media is None
                                else {
                                    "media_type": block.media.media_type,
                                    "embedded_path": block.media.embedded_path,
                                    "width": block.media.width,
                                    "height": block.media.height,
                                }
                            ),
                            "confidence": block.confidence,
                            "parser_name": block.parser_name,
                            "parser_version": block.parser_version,
                            "warnings": [
                                {
                                    "code": warning.code,
                                    "message": warning.message,
                                    "page_number": warning.page_number,
                                }
                                for warning in block.warnings
                            ],
                        }
                        for block in document.blocks
                    ],
                )
            connection.execute(
                insert(document_chunks),
                [
                    {
                        "id": chunk.id.value,
                        "tenant_id": source.job.tenant_id.value,
                        "knowledge_base_id": source.job.knowledge_base_id.value,
                        "document_id": source.job.document_id.value,
                        "document_version_id": source.job.document_version_id.value,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "source": dict(chunk.source),
                    }
                    for chunk in document.chunks
                ],
            )
            inherited: list[dict[str, object]] = []
            if active is not None:
                inherited_rows = connection.execute(
                    select(chunk_embeddings)
                    .join(
                        document_chunks,
                        document_chunks.c.id == chunk_embeddings.c.chunk_id,
                    )
                    .where(
                        chunk_embeddings.c.index_version_id == active["id"],
                        document_chunks.c.document_id != source.job.document_id.value,
                    )
                ).mappings()
                inherited = [
                    {
                        "index_version_id": index_version_id.value,
                        "chunk_id": row["chunk_id"],
                        "tenant_id": row["tenant_id"],
                        "knowledge_base_id": row["knowledge_base_id"],
                        "embedding": row["embedding"],
                    }
                    for row in inherited_rows
                ]
            current = [
                {
                    "index_version_id": index_version_id.value,
                    "chunk_id": chunk.id.value,
                    "tenant_id": source.job.tenant_id.value,
                    "knowledge_base_id": source.job.knowledge_base_id.value,
                    "embedding": list(vector),
                }
                for chunk, vector in zip(document.chunks, vectors, strict=True)
            ]
            connection.execute(insert(chunk_embeddings), inherited + current)
            total_count = len(inherited) + len(current)
            connection.execute(
                update(index_versions)
                .where(index_versions.c.id == index_version_id.value)
                .values(chunk_count=total_count)
            )
        return StagedGeneration(index_version_id, generation, total_count, dimensions)

    def validate_generation(self, value: StagedGeneration) -> None:
        with self._engine.connect() as connection:
            count = connection.scalar(
                select(func.count())
                .select_from(chunk_embeddings)
                .where(chunk_embeddings.c.index_version_id == value.index_version_id.value)
            )
        if count != value.chunk_count or value.chunk_count < 1 or value.vector_dimensions != 8:
            raise ValueError("candidate generation failed validation")

    def publish_generation(
        self, source: IngestionSource, value: StagedGeneration, now: datetime
    ) -> IngestionJobRecord:
        with self._engine.begin() as connection:
            connection.execute(
                update(index_versions)
                .where(
                    index_versions.c.tenant_id == source.job.tenant_id.value,
                    index_versions.c.knowledge_base_id == source.job.knowledge_base_id.value,
                    index_versions.c.status == "active",
                )
                .values(status="superseded")
            )
            connection.execute(
                update(index_versions)
                .where(index_versions.c.id == value.index_version_id.value)
                .values(status="active", published_at=now)
            )
            connection.execute(
                update(document_versions)
                .where(
                    document_versions.c.tenant_id == source.job.tenant_id.value,
                    document_versions.c.document_id == source.job.document_id.value,
                    document_versions.c.status == "active",
                    document_versions.c.id != source.job.document_version_id.value,
                )
                .values(status="superseded")
            )
            connection.execute(
                update(document_versions)
                .where(document_versions.c.id == source.job.document_version_id.value)
                .values(status="active", activated_at=now)
            )
            connection.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.id == source.job.id.value)
                .values(status="succeeded", progress=1.0, updated_at=now)
            )
            row = (
                connection.execute(
                    select(ingestion_jobs).where(ingestion_jobs.c.id == source.job.id.value)
                )
                .mappings()
                .one()
            )
        return self._job(row)

    def fail_ingestion(self, job_id: JobId, *, code: str, message: str, now: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.id == job_id.value)
                .values(
                    status="failed",
                    error_code=code,
                    error_message=message,
                    updated_at=now,
                )
            )

    def search(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        query_vector: tuple[float, ...],
        top_k: int,
    ) -> tuple[SearchHit, ...]:
        for knowledge_base_id in knowledge_base_ids:
            CorePolicies.require_knowledge_base(context, knowledge_base_id)
        ids = [value.value for value in knowledge_base_ids]
        distance = chunk_embeddings.c.embedding.cosine_distance(list(query_vector))
        statement = (
            select(document_chunks, (1.0 - distance).label("score"))
            .join(
                chunk_embeddings,
                chunk_embeddings.c.chunk_id == document_chunks.c.id,
            )
            .join(
                index_versions,
                index_versions.c.id == chunk_embeddings.c.index_version_id,
            )
            .join(
                document_versions,
                document_versions.c.id == document_chunks.c.document_version_id,
            )
            .where(
                document_chunks.c.tenant_id == context.tenant_id.value,
                chunk_embeddings.c.tenant_id == context.tenant_id.value,
                index_versions.c.tenant_id == context.tenant_id.value,
                document_chunks.c.knowledge_base_id.in_(ids),
                chunk_embeddings.c.knowledge_base_id.in_(ids),
                index_versions.c.knowledge_base_id.in_(ids),
                index_versions.c.status == "active",
                document_versions.c.status == "active",
            )
            .order_by(distance, document_chunks.c.id)
            .limit(top_k)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            SearchHit(
                TenantId(row["tenant_id"]),
                KnowledgeBaseId(row["knowledge_base_id"]),
                DocumentId(row["document_id"]),
                DocumentVersionId(row["document_version_id"]),
                ChunkId(row["id"]),
                str(row["text"]),
                cast(dict[str, str], row["source"]),
                float(row["score"]),
                rank,
            )
            for rank, row in enumerate(rows, start=1)
        )

    def save_trace(self, value: RetrievalTraceRecord) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(retrieval_traces).values(
                    id=value.id.value,
                    tenant_id=value.tenant_id.value,
                    knowledge_base_ids=[item.value for item in value.knowledge_base_ids],
                    query_sha256=value.query_sha256,
                    status=value.status,
                    candidate_count=value.candidate_count,
                    selected_chunk_ids=[item.value for item in value.selected_chunk_ids],
                    authorization_applied=value.authorization_applied,
                    created_at=value.created_at,
                    canonical_query_sha256=value.canonical_query_sha256,
                    query_variant_sha256=list(value.query_variant_sha256),
                    events=list(value.events),
                    candidate_traces=list(value.candidate_traces),
                    fallback_steps=list(value.fallback_steps),
                    filter_summary=list(value.filter_summary),
                    provider_ids=list(value.provider_ids),
                    completed_at=value.completed_at,
                    expires_at=value.expires_at,
                    error_code=value.error_code,
                    request_id=value.request_id,
                    index_version_ids=[item.value for item in value.index_version_ids],
                )
            )

    def get_trace(
        self, context: AuthorizationContext, trace_id: TraceId
    ) -> RetrievalTraceRecord | None:
        CorePolicies.require_role(context, "owner", "admin", "auditor")
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(retrieval_traces).where(
                        retrieval_traces.c.id == trace_id.value,
                        retrieval_traces.c.tenant_id == context.tenant_id.value,
                        or_(
                            retrieval_traces.c.expires_at.is_(None),
                            retrieval_traces.c.expires_at > func.now(),
                        ),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return RetrievalTraceRecord(
            TraceId(row["id"]),
            TenantId(row["tenant_id"]),
            tuple(KnowledgeBaseId(value) for value in row["knowledge_base_ids"]),
            str(row["query_sha256"]),
            str(row["status"]),
            int(row["candidate_count"]),
            tuple(ChunkId(value) for value in row["selected_chunk_ids"]),
            bool(row["authorization_applied"]),
            cast(datetime, row["created_at"]),
            str(row["canonical_query_sha256"]),
            tuple(str(value) for value in row["query_variant_sha256"]),
            tuple(cast(dict[str, object], value) for value in row["events"]),
            tuple(cast(dict[str, object], value) for value in row["candidate_traces"]),
            tuple(cast(dict[str, object], value) for value in row["fallback_steps"]),
            tuple(str(value) for value in row["filter_summary"]),
            tuple(str(value) for value in row["provider_ids"]),
            cast(datetime | None, row["completed_at"]),
            cast(datetime | None, row["expires_at"]),
            cast(str | None, row["error_code"]),
            cast(str | None, row["request_id"]),
            tuple(IndexVersionId(value) for value in row["index_version_ids"]),
        )

    def validate_search_hits(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        hits: tuple[SearchHit, ...],
    ) -> tuple[SearchHit, ...]:
        """Revalidate projection candidates against current PostgreSQL authority."""

        if not hits:
            return ()
        for knowledge_base_id in knowledge_base_ids:
            CorePolicies.require_knowledge_base(context, knowledge_base_id)
        allowed_ids = [value.value for value in knowledge_base_ids]
        visibility = [
            knowledge_bases.c.visibility == "tenant",
            knowledge_bases.c.owner_id == context.actor_id.value,
        ]
        if "admin" in context.roles:
            visibility.append(knowledge_bases.c.status == "active")
        statement = (
            select(document_chunks.c.id)
            .join(
                document_versions,
                document_versions.c.id == document_chunks.c.document_version_id,
            )
            .join(
                knowledge_bases,
                knowledge_bases.c.id == document_chunks.c.knowledge_base_id,
            )
            .join(chunk_embeddings, chunk_embeddings.c.chunk_id == document_chunks.c.id)
            .join(index_versions, index_versions.c.id == chunk_embeddings.c.index_version_id)
            .where(
                document_chunks.c.id.in_([value.chunk_id.value for value in hits]),
                document_chunks.c.tenant_id == context.tenant_id.value,
                document_chunks.c.knowledge_base_id.in_(allowed_ids),
                document_versions.c.tenant_id == context.tenant_id.value,
                document_versions.c.status == "active",
                knowledge_bases.c.tenant_id == context.tenant_id.value,
                knowledge_bases.c.status == "active",
                or_(*visibility),
                index_versions.c.tenant_id == context.tenant_id.value,
                index_versions.c.knowledge_base_id.in_(allowed_ids),
                index_versions.c.status == "active",
            )
            .distinct()
        )
        with self._engine.connect() as connection:
            authorized = set(connection.scalars(statement))
        return tuple(hit for hit in hits if hit.chunk_id.value in authorized)

    def document_version_status(
        self, document_version_id: DocumentVersionId
    ) -> VersionStatus | None:
        with self._engine.connect() as connection:
            value = connection.scalar(
                select(document_versions.c.status).where(
                    document_versions.c.id == document_version_id.value
                )
            )
        return None if value is None else VersionStatus(str(value))

    @staticmethod
    def _knowledge_base(row: Any) -> KnowledgeBaseRecord:
        return KnowledgeBaseRecord(
            KnowledgeBaseId(row["id"]),
            TenantId(row["tenant_id"]),
            ActorId(row["owner_id"]),
            str(row["name"]),
            str(row["description"]),
            str(row["visibility"]),
            str(row["status"]),
            cast(datetime, row["created_at"]),
            cast(datetime, row["updated_at"]),
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
        )

    @classmethod
    def _source(cls, row: Any) -> IngestionSource:
        return IngestionSource(
            cls._job(row),
            str(row["file_name"]),
            str(row["media_type"]),
            str(row["object_key"]),
            str(row["source_sha256"]),
            str(row["chunk_method"]),
        )
