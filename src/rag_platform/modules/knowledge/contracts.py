"""Framework-neutral contracts for the R2 knowledge vertical slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.entities import VersionStatus, WorkStatus
from rag_platform.domain.identifiers import (
    ActorId,
    BlockId,
    ChunkId,
    DocumentId,
    DocumentVersionId,
    IndexVersionId,
    JobId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)


class UnsupportedDocument(ValueError):
    """The R2 compiler only accepts bounded UTF-8 TXT and Markdown."""


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for different input."""


@dataclass(frozen=True, slots=True)
class KnowledgeBaseRecord:
    id: KnowledgeBaseId
    tenant_id: TenantId
    owner_id: ActorId
    name: str
    description: str
    visibility: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IngestionJobRecord:
    id: JobId
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    requested_by: ActorId
    idempotency_key: str
    trace_id: TraceId
    status: WorkStatus
    progress: float
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class UploadSubmission:
    job: IngestionJobRecord
    duplicate: bool


@dataclass(frozen=True, slots=True)
class IngestionSource:
    job: IngestionJobRecord
    file_name: str
    media_type: str
    object_key: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class CompiledBlock:
    id: BlockId
    ordinal: int
    kind: str
    text: str
    start_character: int
    end_character: int


@dataclass(frozen=True, slots=True)
class CompiledChunk:
    id: ChunkId
    ordinal: int
    text: str
    source: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


@dataclass(frozen=True, slots=True)
class CompiledDocument:
    blocks: tuple[CompiledBlock, ...]
    chunks: tuple[CompiledChunk, ...]
    normalized_sha256: str
    compiler_version: str


@dataclass(frozen=True, slots=True)
class StagedGeneration:
    index_version_id: IndexVersionId
    generation: int
    chunk_count: int
    vector_dimensions: int


@dataclass(frozen=True, slots=True)
class SearchHit:
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    chunk_id: ChunkId
    content: str
    source: Mapping[str, str]
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class RetrievalTraceRecord:
    id: TraceId
    tenant_id: TenantId
    knowledge_base_ids: tuple[KnowledgeBaseId, ...]
    query_sha256: str
    status: str
    candidate_count: int
    selected_chunk_ids: tuple[ChunkId, ...]
    authorization_applied: bool
    created_at: datetime


class KnowledgeRepository(Protocol):
    """Authority plus vector-index port; every public read is tenant scoped."""

    def create_knowledge_base(self, value: KnowledgeBaseRecord) -> None: ...

    def get_knowledge_base(
        self, context: AuthorizationContext, knowledge_base_id: KnowledgeBaseId
    ) -> KnowledgeBaseRecord | None: ...

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
        now: datetime,
    ) -> UploadSubmission: ...

    def get_job(
        self, context: AuthorizationContext, job_id: JobId
    ) -> IngestionJobRecord | None: ...

    def next_pending_job(self) -> JobId | None: ...

    def begin_ingestion(self, job_id: JobId, now: datetime) -> IngestionSource: ...

    def stage_generation(
        self,
        source: IngestionSource,
        document: CompiledDocument,
        vectors: tuple[tuple[float, ...], ...],
        embedding_model_id: str,
        now: datetime,
    ) -> StagedGeneration: ...

    def validate_generation(self, value: StagedGeneration) -> None: ...

    def publish_generation(
        self, source: IngestionSource, value: StagedGeneration, now: datetime
    ) -> IngestionJobRecord: ...

    def fail_ingestion(self, job_id: JobId, *, code: str, message: str, now: datetime) -> None: ...

    def search(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        query_vector: tuple[float, ...],
        top_k: int,
    ) -> tuple[SearchHit, ...]: ...

    def save_trace(self, value: RetrievalTraceRecord) -> None: ...

    def get_trace(
        self, context: AuthorizationContext, trace_id: TraceId
    ) -> RetrievalTraceRecord | None: ...

    def document_version_status(
        self, document_version_id: DocumentVersionId
    ) -> VersionStatus | None: ...
