"""Core RAG entities; deliberately independent of frameworks and persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from rag_platform.domain.identifiers import (
    AgentRunId,
    BlockId,
    ChunkId,
    DocumentId,
    DocumentVersionId,
    IndexVersionId,
    JobId,
    KnowledgeBaseId,
    OperationId,
    TenantId,
    TraceId,
)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamps must be timezone-aware UTC")


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class WorkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Tenant:
    id: TenantId
    name: str
    created_at: datetime
    status: TenantStatus = TenantStatus.ACTIVE

    def __post_init__(self) -> None:
        _require_text(self.name, "tenant name")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    id: KnowledgeBaseId
    tenant_id: TenantId
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.name, "knowledge base name")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class Document:
    id: DocumentId
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    external_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.external_key, "document external key")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    id: DocumentVersionId
    tenant_id: TenantId
    document_id: DocumentId
    revision: int
    source_sha256: str
    created_at: datetime
    status: VersionStatus = VersionStatus.DRAFT

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("document revision must be positive")
        if len(self.source_sha256) != 64:
            raise ValueError("source_sha256 must be a SHA-256 hex digest")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class Block:
    id: BlockId
    tenant_id: TenantId
    document_version_id: DocumentVersionId
    ordinal: int
    kind: str
    text: str

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("block ordinal must not be negative")
        _require_text(self.kind, "block kind")


@dataclass(frozen=True, slots=True)
class Chunk:
    id: ChunkId
    tenant_id: TenantId
    document_version_id: DocumentVersionId
    ordinal: int
    text: str
    source: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("chunk ordinal must not be negative")
        _require_text(self.text, "chunk text")
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


@dataclass(frozen=True, slots=True)
class IndexVersion:
    id: IndexVersionId
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    generation: int
    created_at: datetime
    status: VersionStatus = VersionStatus.CANDIDATE

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("index generation must be positive")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: ChunkId
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_version_id: DocumentVersionId
    score: float
    rank: int
    channel: str

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("retrieval rank must be positive")
        _require_text(self.channel, "retrieval channel")


@dataclass(frozen=True, slots=True)
class Citation:
    chunk_id: ChunkId
    document_version_id: DocumentVersionId
    quote: str
    page: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.quote, "citation quote")
        if self.page is not None and self.page < 1:
            raise ValueError("citation page must be positive")


@dataclass(frozen=True, slots=True)
class Trace:
    id: TraceId
    tenant_id: TenantId
    created_at: datetime
    events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class Job:
    id: JobId
    tenant_id: TenantId
    kind: str
    created_at: datetime
    status: WorkStatus = WorkStatus.PENDING

    def __post_init__(self) -> None:
        _require_text(self.kind, "job kind")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class Operation:
    id: OperationId
    tenant_id: TenantId
    kind: str
    created_at: datetime
    status: WorkStatus = WorkStatus.PENDING

    def __post_init__(self) -> None:
        _require_text(self.kind, "operation kind")
        _require_utc(self.created_at)


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: AgentRunId
    tenant_id: TenantId
    created_at: datetime
    status: WorkStatus = WorkStatus.PENDING

    def __post_init__(self) -> None:
        _require_utc(self.created_at)
