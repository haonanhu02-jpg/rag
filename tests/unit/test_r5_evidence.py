from __future__ import annotations

import json
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
from rag_platform.modules.grounded_rag.contracts import CitationIntegrityError, EvidenceStatus
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
