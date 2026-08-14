from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
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
from rag_platform.modules.grounded_rag import (
    CancellationToken,
    CitationIntegrityError,
    EvidenceStatus,
    GenerationBudget,
    GenerationBudgetExceeded,
    GenerationCancelled,
    GroundedRag,
)
from rag_platform.modules.knowledge.contracts import RetrievalTraceRecord, SearchHit
from rag_platform.modules.model_runtime import FakeModelRuntime, ModelKind, ModelRegistration
from rag_platform.modules.retrieval.service import RetrievalResult


class _Retrieval:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits

    def retrieve(self, context: AuthorizationContext, *, request: object) -> RetrievalResult:
        del request
        return RetrievalResult(
            self.hits,
            RetrievalTraceRecord(
                _trace(),
                context.tenant_id,
                (_kb(),),
                "a" * 64,
                "success" if self.hits else "no_evidence",
                len(self.hits),
                tuple(hit.chunk_id for hit in self.hits),
                True,
                datetime(2026, 8, 14, tzinfo=UTC),
            ),
        )


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


def test_sufficient_answer_is_source_bound_and_model_cannot_set_policy_status() -> None:
    service, models = _service((_hit(1),), response="Reset the relay [1].")

    answer = service.answer(_context(), question="reset?", knowledge_base_ids=(_kb(),))

    assert answer.status == "answered"
    assert answer.evidence_status is EvidenceStatus.SUFFICIENT
    assert answer.citations[0].chunk_id == _hit(1).chunk_id
    assert answer.citations[0].trace_id == answer.trace_id
    assert answer.model_id == "chat"
    assert models.chat_requests[-1].metadata["evidence_status"] == "sufficient"


def test_no_evidence_and_conflict_are_deterministic_refusals_without_model_call() -> None:
    empty, empty_models = _service((), response="must not run")
    no_evidence = empty.answer(_context(), question="missing", knowledge_base_ids=(_kb(),))
    conflict_hits = (
        _hit(1, source={"conflict_key": "relay", "claim": "open"}),
        _hit(2, source={"conflict_key": "relay", "claim": "closed"}),
    )
    conflict, conflict_models = _service(conflict_hits, response="must not run")
    conflicted = conflict.answer(_context(), question="state?", knowledge_base_ids=(_kb(),))

    assert no_evidence.status == "no_evidence"
    assert no_evidence.citations == ()
    assert empty_models.chat_requests == []
    assert conflicted.status == "conflicting_evidence"
    assert conflicted.evidence_status is EvidenceStatus.CONFLICTING_EVIDENCE
    assert len(conflicted.citations) == 2
    assert conflict_models.chat_requests == []


def test_partial_evidence_model_fallback_and_budget_are_governed() -> None:
    fallback_only = FakeModelRuntime(
        (ModelRegistration("fallback", "fake", "fake", ModelKind.CHAT),),
        chat_response="Limited evidence [1].",
    )
    service = GroundedRag(
        retrieval=cast(Any, _Retrieval((_hit(1, score=0.2),))),
        models=fallback_only,
        authority=_Authority(),
        chat_model_id="missing",
        fallback_chat_model_ids=("fallback",),
        minimum_evidence_score=0.8,
    )

    answer = service.answer(_context(), question="reset?", knowledge_base_ids=(_kb(),))

    assert answer.status == "partial_evidence"
    assert answer.model_id == "fallback"
    assert answer.model_attempts == 2
    assert answer.degradation_steps == ("model:missing:UnknownModel",)

    budgeted, _ = _service(
        (_hit(1),),
        response="one two three four [1]",
        budget=GenerationBudget(max_input_tokens=1000, max_output_tokens=2),
    )
    with pytest.raises(GenerationBudgetExceeded, match="output"):
        budgeted.answer(_context(), question="reset?", knowledge_base_ids=(_kb(),))


def test_citation_is_revalidated_after_generation_and_unknown_markers_fail() -> None:
    revoked, _ = _service((_hit(1),), response="answer [1]", authority=_Authority(active=False))
    unknown, _ = _service((_hit(1),), response="answer [9]")

    with pytest.raises(CitationIntegrityError, match="authority changed"):
        revoked.answer(_context(), question="q", knowledge_base_ids=(_kb(),))
    with pytest.raises(CitationIntegrityError, match="unknown"):
        unknown.answer(_context(), question="q", knowledge_base_ids=(_kb(),))


def test_stream_sequence_fallback_completion_and_cancellation() -> None:
    fallback_only = FakeModelRuntime(
        (ModelRegistration("fallback", "fake", "fake", ModelKind.CHAT),),
        chat_response="reset relay [1]",
    )
    service = GroundedRag(
        retrieval=cast(Any, _Retrieval((_hit(1),))),
        models=fallback_only,
        authority=_Authority(),
        chat_model_id="missing",
        fallback_chat_model_ids=("fallback",),
    )
    events = tuple(
        service.stream_answer(_context(), question="reset?", knowledge_base_ids=(_kb(),))
    )

    assert [item.sequence for item in events] == list(range(len(events)))
    assert [item.event for item in events[:3]] == [
        "retrieval_started",
        "evidence_evaluated",
        "model_fallback",
    ]
    assert events[-2].event == "citations"
    assert events[-1].event == "completed"
    assert events[-1].answer is not None
    assert events[-1].answer.model_id == "fallback"
    assert "".join(item.delta for item in events if item.event == "answer_delta") == (
        "reset relay [1]"
    )

    cancellation = CancellationToken()
    cancellation.cancel()
    with pytest.raises(GenerationCancelled):
        service.answer(
            _context(), question="reset?", knowledge_base_ids=(_kb(),), cancellation=cancellation
        )
    cancelled = tuple(
        service.stream_answer(
            _context(),
            question="reset?",
            knowledge_base_ids=(_kb(),),
            cancellation=cancellation,
        )
    )
    assert [item.event for item in cancelled] == ["retrieval_started", "cancelled"]


def _service(
    hits: tuple[SearchHit, ...],
    *,
    response: str,
    authority: _Authority | None = None,
    budget: GenerationBudget | None = None,
) -> tuple[GroundedRag, FakeModelRuntime]:
    models = FakeModelRuntime(
        (ModelRegistration("chat", "fake", "fake", ModelKind.CHAT),),
        chat_response=response,
    )
    return (
        GroundedRag(
            retrieval=cast(Any, _Retrieval(hits)),
            models=models,
            authority=authority or _Authority(),
            chat_model_id="chat",
            generation_budget=budget,
        ),
        models,
    )


def _hit(index: int, *, score: float = 1.0, source: dict[str, str] | None = None) -> SearchHit:
    return SearchHit(
        _tenant(),
        _kb(),
        DocumentId(UUID(int=10 + index)),
        DocumentVersionId(UUID(int=20 + index)),
        ChunkId(UUID(int=30 + index)),
        f"relay reset evidence {index}",
        source or {"file_name": "manual.txt", "media_type": "text/plain"},
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
