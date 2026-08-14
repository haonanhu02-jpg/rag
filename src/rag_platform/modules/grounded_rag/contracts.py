"""Framework-neutral contracts for fixed, evidence-grounded RAG."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event
from types import MappingProxyType
from typing import Protocol

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import (
    ChunkId,
    DocumentId,
    DocumentVersionId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)
from rag_platform.modules.knowledge.contracts import SearchHit


class EvidenceStatus(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL_EVIDENCE = "partial_evidence"
    NO_EVIDENCE = "no_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class CitationIntegrityError(RuntimeError):
    """A generated citation cannot be proven against current authority."""


class GenerationBudgetExceeded(RuntimeError):
    """A configured input, output, or cost ceiling was crossed."""


class GenerationCancelled(RuntimeError):
    """Generation was cancelled before a final answer was published."""


@dataclass(frozen=True, slots=True)
class GenerationBudget:
    max_input_tokens: int = 16_000
    max_output_tokens: int = 2_000
    max_cost_microunits: int = 1_000_000

    def __post_init__(self) -> None:
        if min(self.max_input_tokens, self.max_output_tokens, self.max_cost_microunits) < 1:
            raise ValueError("generation budget values must be positive")


class CancellationToken:
    """Thread-safe cooperative cancellation shared by HTTP and model adapters."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise GenerationCancelled("generation cancelled")


@dataclass(frozen=True, slots=True)
class RagBoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: str

    def __post_init__(self) -> None:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("citation bounding box must have positive dimensions")
        if not self.coordinate_space.strip():
            raise ValueError("citation coordinate space must not be empty")


@dataclass(frozen=True, slots=True)
class RagCitation:
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    chunk_id: ChunkId
    quote: str
    source: Mapping[str, str]
    trace_id: TraceId
    page_number: int | None = None
    bounding_box: RagBoundingBox | None = None
    source_uri: str | None = None
    media_kind: str | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if not self.quote.strip():
            raise ValueError("citation quote must not be empty")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("citation page number must be positive")
        if self.bounding_box is not None and self.page_number is None:
            raise ValueError("citation bounding box requires a page number")
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    index: int
    hit: SearchHit
    citation: RagCitation
    normalized_score: float

    def __post_init__(self) -> None:
        if self.index < 1 or not 0 <= self.normalized_score <= 1:
            raise ValueError("invalid evidence item")


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    status: EvidenceStatus
    reason: str
    eligible_indices: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidencePackage:
    question: str
    trace_id: TraceId
    items: tuple[EvidenceItem, ...]
    decision: EvidenceDecision
    context_text: str


@dataclass(frozen=True, slots=True)
class FixedRagAnswer:
    status: str
    answer: str
    citations: tuple[RagCitation, ...]
    trace_id: TraceId
    prompt_version: str
    evidence_status: EvidenceStatus = EvidenceStatus.NO_EVIDENCE
    evidence_reason: str = ""
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_microunits: int = 0
    model_attempts: int = 0
    degradation_steps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RagStreamEvent:
    sequence: int
    event: str
    delta: str = ""
    answer: FixedRagAnswer | None = None
    attributes: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0 or not self.event.strip():
            raise ValueError("invalid stream event")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


class CitationAuthority(Protocol):
    def validate_search_hits(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        hits: tuple[SearchHit, ...],
    ) -> tuple[SearchHit, ...]:
        ...
