"""The single authorized hybrid-retrieval boundary for RAG and future tools."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import TraceId
from rag_platform.domain.policies import CorePolicies, ResourceNotFound
from rag_platform.modules.knowledge.contracts import (
    KnowledgeRepository,
    RetrievalTraceRecord,
    SearchHit,
)
from rag_platform.modules.model_runtime.contracts import EmbeddingRequest, ModelRuntime
from rag_platform.modules.ports import Clock, IdGenerator
from rag_platform.modules.retrieval.contracts import (
    MetadataFilter,
    MetadataFilterGroup,
    NullRetrievalMetrics,
    RetrievalMetrics,
    RetrievalRequest,
    SearchDependencyError,
    SearchReader,
    SearchScope,
)
from rag_platform.modules.retrieval.query import QueryProcessor
from rag_platform.modules.retrieval.ranking import (
    reciprocal_rank_fusion,
    rerank_candidates,
    select_candidates,
)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: tuple[SearchHit, ...]
    trace: RetrievalTraceRecord
    empty_reason: str | None = None


class AuthorizedRetrieval:
    """Owns query transformation, ranking, fallback, and final authority checks."""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        search: SearchReader,
        models: ModelRuntime,
        embedding_model_id: str,
        reranker_model_id: str,
        query_processor: QueryProcessor,
        trace_ids: IdGenerator[TraceId],
        clock: Clock,
        metrics: RetrievalMetrics | None = None,
        minimum_score: float = 0.0,
        per_document_quota: int = 3,
        trace_ttl: timedelta = timedelta(days=7),
    ) -> None:
        if per_document_quota < 1 or trace_ttl <= timedelta(0):
            raise ValueError("invalid retrieval policy")
        self._repository = repository
        self._search = search
        self._models = models
        self._embedding_model_id = embedding_model_id
        self._reranker_model_id = reranker_model_id
        self._query_processor = query_processor
        self._trace_ids = trace_ids
        self._clock = clock
        self._metrics = metrics or NullRetrievalMetrics()
        self._minimum_score = minimum_score
        self._per_document_quota = per_document_quota
        self._trace_ttl = trace_ttl

    def retrieve(
        self, context: AuthorizationContext, *, request: RetrievalRequest
    ) -> RetrievalResult:
        started_at = self._now()
        started_clock = monotonic()
        trace_id = self._trace_ids.new()
        events: list[dict[str, object]] = []
        fallback_steps: list[dict[str, object]] = []
        for knowledge_base_id in request.knowledge_base_ids:
            CorePolicies.require_knowledge_base(context, knowledge_base_id)
            if self._repository.get_knowledge_base(context, knowledge_base_id) is None:
                raise ResourceNotFound("knowledge base not found")
        self._event(events, "authorization", started_clock, 0)
        processed = self._query_processor.process(request)
        self._event(events, "preprocess", started_clock, len(processed.variants))
        base_scope = SearchScope(
            context.tenant_id,
            context.actor_id,
            context.roles,
            request.knowledge_base_ids,
            processed.variants,
            processed.keywords,
            request.top_k,
            request.user_filter,
            request.inferred_filter or processed.inferred_filter,
        )
        dependency_errors: list[str] = []
        fused = self._hybrid(
            base_scope, processed.canonical, events, started_clock, dependency_errors
        )
        ranked, reranker_error = rerank_candidates(
            self._models,
            model_id=self._reranker_model_id,
            query=processed.canonical,
            candidates=fused,
        )
        self._event(
            events,
            "rerank",
            started_clock,
            len(ranked),
            error=reranker_error or "none",
        )
        selected = select_candidates(
            ranked,
            top_n=request.top_n,
            threshold=self._minimum_score,
            per_document_quota=self._per_document_quota,
        )
        if not selected:
            selected, fallback_ranked = self._fallback(
                base_scope,
                processed.canonical,
                request.top_n,
                events,
                fallback_steps,
                dependency_errors,
                started_clock,
            )
            if fallback_ranked:
                ranked = fallback_ranked
        if not selected and dependency_errors:
            trace = self._trace(
                trace_id,
                context,
                request,
                processed,
                "failed",
                ranked,
                (),
                events,
                fallback_steps,
                started_at,
                "search_dependency_failed",
            )
            self._persist_trace(trace)
            raise SearchDependencyError("; ".join(dict.fromkeys(dependency_errors)))
        authorized = self._repository.validate_search_hits(
            context, request.knowledge_base_ids, selected
        )
        self._event(events, "select", started_clock, len(authorized))
        status = "success" if authorized else "no_evidence"
        trace = self._trace(
            trace_id,
            context,
            request,
            processed,
            status,
            ranked,
            authorized,
            events,
            fallback_steps,
            started_at,
            None,
        )
        self._persist_trace(trace)
        return RetrievalResult(
            authorized,
            trace,
            None if authorized else ("permission_filtered" if selected else "no_match"),
        )

    def _hybrid(
        self,
        scope: SearchScope,
        canonical: str,
        events: list[dict[str, object]],
        started: float,
        errors: list[str],
        *,
        channels: tuple[str, ...] = ("full_text", "vector"),
    ) -> tuple[SearchHit, ...]:
        full_text: tuple[SearchHit, ...] = ()
        vector: tuple[SearchHit, ...] = ()

        def vector_search() -> tuple[SearchHit, ...]:
            embedded = self._models.embed(EmbeddingRequest(self._embedding_model_id, (canonical,)))
            return self._search.vector(scope, embedded.vectors[0])

        with ThreadPoolExecutor(max_workers=len(channels)) as executor:
            futures = {}
            if "full_text" in channels:
                futures["full_text"] = executor.submit(self._search.full_text, scope)
            if "vector" in channels:
                futures["vector"] = executor.submit(vector_search)
            for channel, future in futures.items():
                try:
                    values = future.result()
                    if channel == "full_text":
                        full_text = values
                    else:
                        vector = values
                    self._event(events, channel, started, len(values))
                except Exception as exc:
                    errors.append(f"{channel}:{type(exc).__name__}")
                    self._event(events, channel, started, 0, error=type(exc).__name__)
        fused = reciprocal_rank_fusion(full_text, vector)
        self._event(events, "fusion", started, len(fused))
        return fused[: scope.top_k]

    def _fallback(
        self,
        scope: SearchScope,
        canonical: str,
        top_n: int,
        events: list[dict[str, object]],
        steps: list[dict[str, object]],
        errors: list[str],
        started: float,
    ) -> tuple[tuple[SearchHit, ...], tuple[SearchHit, ...]]:
        plans: list[
            tuple[str, MetadataFilter | MetadataFilterGroup | None, tuple[str, ...], int]
        ] = [
            ("expanded_hybrid", scope.inferred_filter, ("full_text", "vector"), scope.top_k * 2),
        ]
        if scope.inferred_filter is not None:
            plans.append(("soft_filter_removed", None, ("full_text", "vector"), scope.top_k * 2))
        plans.extend(
            (
                ("full_text_only", scope.inferred_filter, ("full_text",), scope.top_k),
                ("vector_only", scope.inferred_filter, ("vector",), scope.top_k),
            )
        )
        for attempt, (mode, inferred, channels, top_k) in enumerate(plans, start=1):
            active = SearchScope(
                scope.tenant_id,
                scope.actor_id,
                scope.roles,
                scope.knowledge_base_ids,
                scope.variants,
                scope.keywords,
                min(top_k, 1000),
                scope.user_filter,
                inferred,
            )
            fused = self._hybrid(active, canonical, events, started, errors, channels=channels)
            reranked, _ = rerank_candidates(
                self._models,
                model_id=self._reranker_model_id,
                query=canonical,
                candidates=fused,
            )
            selected = select_candidates(
                reranked,
                top_n=top_n,
                threshold=self._minimum_score / 2,
                per_document_quota=self._per_document_quota,
            )
            steps.append(
                {
                    "attempt": attempt,
                    "mode": mode,
                    "reason": "empty_or_below_threshold",
                    "candidate_top_k": active.top_k,
                    "threshold": self._minimum_score / 2,
                    "inferred_filter_removed": scope.inferred_filter is not None
                    and inferred is None,
                    "result_count": len(selected),
                }
            )
            self._event(events, "fallback", started, len(selected), mode=mode)
            if selected:
                return selected, reranked
        return (), ()

    def _trace(
        self,
        trace_id: TraceId,
        context: AuthorizationContext,
        request: RetrievalRequest,
        processed: object,
        status: str,
        ranked: tuple[SearchHit, ...],
        selected: tuple[SearchHit, ...],
        events: list[dict[str, object]],
        fallback_steps: list[dict[str, object]],
        started_at: datetime,
        error_code: str | None,
    ) -> RetrievalTraceRecord:
        from rag_platform.modules.retrieval.contracts import ProcessedQuery

        if not isinstance(processed, ProcessedQuery):
            raise TypeError("invalid processed query")
        completed_at = self._now()
        selected_ids = {item.chunk_id for item in selected}
        traces = tuple(
            {
                "knowledge_base_id": str(item.knowledge_base_id),
                "document_id": str(item.document_id),
                "document_version_id": str(item.document_version_id),
                "chunk_id": str(item.chunk_id),
                "index_version_id": (
                    None if item.index_version_id is None else str(item.index_version_id)
                ),
                "full_text_rank": item.full_text_rank,
                "full_text_score": item.full_text_score,
                "vector_rank": item.vector_rank,
                "vector_score": item.vector_score,
                "fusion_rank": next(
                    (
                        rank
                        for rank, candidate in enumerate(ranked, 1)
                        if candidate.chunk_id == item.chunk_id
                    ),
                    None,
                ),
                "fusion_score": item.fusion_score,
                "rerank_rank": item.rerank_rank,
                "rerank_score": item.rerank_score,
                "final_rank": next(
                    (
                        rank
                        for rank, candidate in enumerate(selected, 1)
                        if candidate.chunk_id == item.chunk_id
                    ),
                    None,
                ),
                "selected": item.chunk_id in selected_ids,
                "final_status": "selected" if item.chunk_id in selected_ids else "excluded",
                "exclusion_reason": None if item.chunk_id in selected_ids else "not_selected",
            }
            for item in ranked
        )
        return RetrievalTraceRecord(
            id=trace_id,
            tenant_id=context.tenant_id,
            knowledge_base_ids=request.knowledge_base_ids,
            query_sha256=_digest(request.query),
            status=status,
            candidate_count=len(ranked),
            selected_chunk_ids=tuple(item.chunk_id for item in selected),
            authorization_applied=True,
            created_at=started_at,
            canonical_query_sha256=_digest(processed.canonical),
            query_variant_sha256=tuple(_digest(item.text) for item in processed.variants),
            events=tuple(events),
            candidate_traces=traces,
            fallback_steps=tuple(fallback_steps),
            filter_summary=_filter_summary(request),
            provider_ids=processed.provider_ids,
            completed_at=completed_at,
            expires_at=completed_at + self._trace_ttl,
            error_code=error_code,
            request_id=request.request_id,
            index_version_ids=tuple(
                dict.fromkeys(
                    item.index_version_id
                    for item in ranked
                    if item.index_version_id is not None
                )
            ),
        )

    def _persist_trace(self, trace: RetrievalTraceRecord) -> None:
        try:
            self._repository.save_trace(trace)
        except Exception:
            self._metrics.increment("retrieval_trace_write_failed")

    @staticmethod
    def _event(
        events: list[dict[str, object]],
        stage: str,
        started: float,
        count: int,
        **attributes: object,
    ) -> None:
        events.append(
            {
                "sequence": len(events),
                "stage": stage,
                "elapsed_ms": round((monotonic() - started) * 1000, 3),
                "candidate_count": count,
                "attributes": attributes,
            }
        )

    def _now(self) -> datetime:
        now = self._clock.now()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise TypeError("clock returned an invalid timestamp")
        return now


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _filter_summary(request: RetrievalRequest) -> tuple[str, ...]:
    return (
        "hard:tenant",
        f"hard:knowledge_bases:{len(request.knowledge_base_ids)}",
        "hard:active_version",
        "hard:not_deleted",
        f"user_ast:{'present' if request.user_filter else 'none'}",
        f"inferred_ast:{'present' if request.inferred_filter else 'none'}",
    )
