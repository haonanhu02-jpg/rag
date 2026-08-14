"""Fixed grounded RAG contracts and service."""

from rag_platform.modules.grounded_rag.contracts import (
    CancellationToken,
    CitationIntegrityError,
    EvidenceDecision,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
    FixedRagAnswer,
    GenerationBudget,
    GenerationBudgetExceeded,
    GenerationCancelled,
    RagBoundingBox,
    RagCitation,
    RagStreamEvent,
)
from rag_platform.modules.grounded_rag.service import (
    CONFLICTING_EVIDENCE_ANSWER,
    FIXED_RAG_PROMPT_VERSION,
    NO_EVIDENCE_ANSWER,
    GroundedRag,
)

__all__ = [
    "CONFLICTING_EVIDENCE_ANSWER",
    "FIXED_RAG_PROMPT_VERSION",
    "NO_EVIDENCE_ANSWER",
    "CancellationToken",
    "CitationIntegrityError",
    "EvidenceDecision",
    "EvidenceItem",
    "EvidencePackage",
    "EvidenceStatus",
    "FixedRagAnswer",
    "GenerationBudget",
    "GenerationBudgetExceeded",
    "GenerationCancelled",
    "GroundedRag",
    "RagBoundingBox",
    "RagCitation",
    "RagStreamEvent",
]
