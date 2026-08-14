from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from rag_platform.domain.authorization import AuthorizationContext, TrustedPrincipal
from rag_platform.domain.identifiers import (
    ActorId,
    ChunkId,
    DocumentId,
    DocumentVersionId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)
from rag_platform.modules.grounded_rag.contracts import (
    CitationIntegrityError,
    EvidenceItem,
    EvidenceStatus,
    GenerationBudget,
    RagBoundingBox,
    RagStreamEvent,
)
from rag_platform.modules.grounded_rag.evidence import (
    EvidenceSufficiencyPolicy,
    build_evidence_package,
    validate_generated_citations,
)
from rag_platform.modules.knowledge.contracts import SearchHit


class _Authority:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active

    def validate_search_hits(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        hits: tuple[SearchHit, ...],
    ) -> tuple[SearchHit, ...]:
        if not self.active:
            return ()
        return tuple(
            hit
            for hit in hits
            if hit.tenant_id == context.tenant_id and hit.knowledge_base_id in knowledge_base_ids
        )


def test_evidence_package_preserves_page_bbox_source_and_trace() -> None:
    hit = _hit(
        1,
        source={
            "file_name": "manual.pdf",
            "media_type": "application/pdf",
            "page_start": "3",
            "page_end": "3",
            "bounding_box": json.dumps(
                {
                    "x0": 1,
                    "y0": 2,
                    "x1": 30,
                    "y1": 40,
                    "coordinate_space": "page_points",
                }
            ),
        },
    )
    package = build_evidence_package(
        "reset?",
        _trace(),
        (hit,),
        policy=EvidenceSufficiencyPolicy(),
        max_context_characters=10_000,
    )

    assert package.decision.status is EvidenceStatus.SUFFICIENT
    citation = package.items[0].citation
    assert citation.page_number == 3
    assert citation.bounding_box is not None
    assert citation.bounding_box.coordinate_space == "page_points"
    assert citation.source_uri == "manual.pdf"
    assert citation.media_kind == "application/pdf"
    assert citation.trace_id == _trace()


def test_policy_distinguishes_no_partial_sufficient_and_conflicting() -> None:
    strict = EvidenceSufficiencyPolicy(minimum_normalized_score=0.8)
    no_evidence = build_evidence_package(
        "q", _trace(), (), policy=strict, max_context_characters=100
    )
    partial = build_evidence_package(
        "q", _trace(), (_hit(1, score=0.2),), policy=strict, max_context_characters=1000
    )
    sufficient = build_evidence_package(
        "q", _trace(), (_hit(1, score=0.9),), policy=strict, max_context_characters=1000
    )
    conflicting = build_evidence_package(
        "q",
        _trace(),
        (
            _hit(1, source={"conflict_key": "relay", "claim": "open"}),
            _hit(2, source={"conflict_key": "relay", "claim": "closed"}),
        ),
        policy=EvidenceSufficiencyPolicy(),
        max_context_characters=2000,
    )

    assert no_evidence.decision.status is EvidenceStatus.NO_EVIDENCE
    assert partial.decision.status is EvidenceStatus.PARTIAL_EVIDENCE
    assert sufficient.decision.status is EvidenceStatus.SUFFICIENT
    assert conflicting.decision.status is EvidenceStatus.CONFLICTING_EVIDENCE


def test_generated_citations_fail_closed_on_unknown_revoked_or_cross_scope_evidence() -> None:
    package = build_evidence_package(
        "q",
        _trace(),
        (_hit(1),),
        policy=EvidenceSufficiencyPolicy(),
        max_context_characters=1000,
    )
    with pytest.raises(CitationIntegrityError, match="unknown"):
        validate_generated_citations(
            "answer [9]",
            package,
            authority=_Authority(),
            context=_context(),
            knowledge_base_ids=(_kb(),),
        )


def test_evidence_contracts_and_malformed_source_locations_fail_safely() -> None:
    with pytest.raises(ValueError, match="within"):
        EvidenceSufficiencyPolicy(minimum_normalized_score=1.1)
    with pytest.raises(ValueError, match="context"):
        build_evidence_package(
            "q", _trace(), (), policy=EvidenceSufficiencyPolicy(), max_context_characters=0
        )
    with pytest.raises(ValueError, match="budget"):
        GenerationBudget(max_input_tokens=0)
    with pytest.raises(ValueError, match="dimensions"):
        RagBoundingBox(1, 1, 0, 2, "pixels")
    with pytest.raises(ValueError, match="coordinate"):
        RagBoundingBox(0, 0, 1, 1, " ")
    with pytest.raises(ValueError, match="stream"):
        RagStreamEvent(-1, "")

    citation = build_evidence_package(
        "q",
        _trace(),
        (_hit(1),),
        policy=EvidenceSufficiencyPolicy(),
        max_context_characters=1000,
    ).items[0].citation
    with pytest.raises(ValueError, match="quote"):
        replace(citation, quote=" ")
    with pytest.raises(ValueError, match="page number"):
        replace(citation, page_number=0)
    with pytest.raises(ValueError, match="requires"):
        replace(citation, bounding_box=RagBoundingBox(0, 0, 1, 1, "pixels"))
    with pytest.raises(ValueError, match="evidence item"):
        EvidenceItem(0, _hit(1), citation, 2.0)

    malformed = (
        _hit(1, score=math.inf, source={"page_start": "x", "bounding_box": "[]"}),
        _hit(2, source={"page_start": "-1", "bounding_box": "not-json"}),
    )
    package = build_evidence_package(
        "q",
        _trace(),
        malformed,
        policy=EvidenceSufficiencyPolicy(),
        max_context_characters=1000,
    )
    assert package.items[0].normalized_score == 0
    assert all(item.citation.page_number is None for item in package.items)


def test_context_bound_and_corrupted_internal_citation_are_rejected() -> None:
    package = build_evidence_package(
        "q",
        _trace(),
        (_hit(1), _hit(2)),
        policy=EvidenceSufficiencyPolicy(),
        max_context_characters=400,
    )
    assert len(package.items) == 1
    item = package.items[0]
    corrupted = replace(item, citation=replace(item.citation, trace_id=TraceId(UUID(int=999))))
    broken_package = replace(package, items=(corrupted,))
    with pytest.raises(CitationIntegrityError, match="quote or trace"):
        validate_generated_citations(
            "answer [1]",
            broken_package,
            authority=_Authority(),
            context=_context(),
            knowledge_base_ids=(_kb(),),
        )
    with pytest.raises(CitationIntegrityError, match="authority changed"):
        validate_generated_citations(
            "answer [1]",
            package,
            authority=_Authority(active=False),
            context=_context(),
            knowledge_base_ids=(_kb(),),
        )
    with pytest.raises(CitationIntegrityError, match="no citation"):
        validate_generated_citations(
            "uncited answer",
            package,
            authority=_Authority(),
            context=_context(),
            knowledge_base_ids=(_kb(),),
        )


def _hit(index: int, *, score: float = 1.0, source: dict[str, str] | None = None) -> SearchHit:
    return SearchHit(
        _tenant(),
        _kb(),
        DocumentId(UUID(int=10 + index)),
        DocumentVersionId(UUID(int=20 + index)),
        ChunkId(UUID(int=30 + index)),
        f"relay reset evidence {index}",
        source or {},
        score,
        index,
        rerank_score=score,
    )


def _context() -> AuthorizationContext:
    return AuthorizationContext.from_principal(
        TrustedPrincipal(_tenant(), ActorId(UUID(int=2)), frozenset({"owner"}))
    )


def _tenant() -> TenantId:
    return TenantId(UUID(int=1))


def _kb() -> KnowledgeBaseId:
    return KnowledgeBaseId(UUID(int=3))


def _trace() -> TraceId:
    return TraceId(UUID(int=4))


def _now() -> datetime:
    return datetime(2026, 8, 14, tzinfo=UTC)
