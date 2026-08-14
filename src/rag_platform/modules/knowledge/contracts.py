"""Framework-neutral contracts for knowledge ingestion and structured documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
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
    """The source format or its declared type cannot be accepted safely."""


class DocumentResourceLimit(UnsupportedDocument):
    """An untrusted document exceeded a deterministic parser resource budget."""

    def __init__(self, resource: str, message: str) -> None:
        super().__init__(message)
        self.resource = resource


class DocumentParseError(UnsupportedDocument):
    """A supported document is malformed or cannot produce usable content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    chunk_method: str = "general"


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    IMAGE = "image"
    CODE = "code"


class CoordinateSpace(StrEnum):
    PAGE_POINTS = "page_points"
    PIXELS = "pixels"
    NORMALIZED = "normalized"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: CoordinateSpace

    def __post_init__(self) -> None:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bounding box must have positive dimensions")
        if self.coordinate_space is CoordinateSpace.NORMALIZED and any(
            value < 0 or value > 1 for value in (self.x0, self.y0, self.x1, self.y1)
        ):
            raise ValueError("normalized coordinates must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class TableMetadata:
    rows: int
    columns: int
    has_header: bool = False

    def __post_init__(self) -> None:
        if self.rows < 1 or self.columns < 1:
            raise ValueError("table dimensions must be positive")


@dataclass(frozen=True, slots=True)
class MediaReference:
    media_type: str
    embedded_path: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class ParseWarning:
    code: str
    message: str
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_file_bytes: int = 10 * 1024 * 1024
    max_archive_entries: int = 2_000
    max_uncompressed_bytes: int = 100 * 1024 * 1024
    max_compression_ratio: float = 200.0
    max_pages: int = 500
    max_image_pixels: int = 40_000_000
    max_worksheets: int = 100
    max_spreadsheet_cells: int = 1_000_000
    ocr_timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class OcrWord:
    text: str
    confidence: float
    order: int
    bounding_box: BoundingBox


@dataclass(frozen=True, slots=True)
class OcrResult:
    engine_name: str
    engine_version: str
    language: str
    words: tuple[OcrWord, ...]


class OcrEngine(Protocol):
    def recognize(self, image: object, *, language: str, timeout_seconds: float) -> OcrResult: ...

    def available_languages(self) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class ParserRequest:
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    source_sha256: str
    file_name: str
    media_type: str
    format_id: str
    limits: ParserLimits
    ocr_language: str


@dataclass(frozen=True, slots=True)
class CompiledBlock:
    id: BlockId
    ordinal: int
    kind: str
    text: str
    start_character: int
    end_character: int
    page_number: int | None = None
    bounding_box: BoundingBox | None = None
    heading_path: tuple[str, ...] = ()
    table: TableMetadata | None = None
    media: MediaReference | None = None
    confidence: float | None = None
    parser_name: str = "plain-text"
    parser_version: str = "1"
    warnings: tuple[ParseWarning, ...] = ()

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.start_character < 0 or self.end_character < 0:
            raise ValueError("block positions must be non-negative")
        if self.bounding_box is not None and self.page_number is None:
            raise ValueError("bounding box requires a page number")
        if self.kind == BlockKind.TABLE and self.table is None:
            raise ValueError("table block requires table metadata")
        if self.kind == BlockKind.IMAGE and self.media is None:
            raise ValueError("image block requires media provenance")
        if self.kind != BlockKind.IMAGE and not self.text.strip():
            raise ValueError("non-image block requires text")


ParsedBlock = CompiledBlock


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    schema_version: int
    parser_name: str
    parser_version: str
    source_media_type: str
    source_name: str
    blocks: tuple[ParsedBlock, ...]
    warnings: tuple[ParseWarning, ...] = ()
    page_count: int | None = None

    def __post_init__(self) -> None:
        orders = [block.ordinal for block in self.blocks]
        if orders != list(range(len(orders))):
            raise ValueError("parsed blocks must have contiguous source order")
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError("parsed block identifiers must be unique")


@dataclass(frozen=True, slots=True)
class ParsedPayload:
    parser_name: str
    parser_version: str
    blocks: tuple[ParsedBlock, ...]
    warnings: tuple[ParseWarning, ...] = ()
    page_count: int | None = None


class BinaryDocumentParser(Protocol):
    format_ids: frozenset[str]

    def parse(self, content: bytes, request: ParserRequest) -> ParsedPayload: ...


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
    parsed_document: ParsedDocument | None = None


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
    full_text_score: float | None = None
    vector_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None
    full_text_rank: int | None = None
    vector_rank: int | None = None
    rerank_rank: int | None = None
    index_version_id: IndexVersionId | None = None


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
    canonical_query_sha256: str = ""
    query_variant_sha256: tuple[str, ...] = ()
    events: tuple[Mapping[str, object], ...] = ()
    candidate_traces: tuple[Mapping[str, object], ...] = ()
    fallback_steps: tuple[Mapping[str, object], ...] = ()
    filter_summary: tuple[str, ...] = ()
    provider_ids: tuple[str, ...] = ()
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    error_code: str | None = None
    request_id: str | None = None
    index_version_ids: tuple[IndexVersionId, ...] = ()


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
        chunk_method: str,
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

    def validate_search_hits(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        hits: tuple[SearchHit, ...],
    ) -> tuple[SearchHit, ...]: ...

    def save_trace(self, value: RetrievalTraceRecord) -> None: ...

    def get_trace(
        self, context: AuthorizationContext, trace_id: TraceId
    ) -> RetrievalTraceRecord | None: ...

    def document_version_status(
        self, document_version_id: DocumentVersionId
    ) -> VersionStatus | None: ...
