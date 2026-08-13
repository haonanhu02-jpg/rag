"""Single authorization boundary used by RAG and future knowledge tools."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import KnowledgeBaseId, TraceId
from rag_platform.domain.policies import CorePolicies, ResourceNotFound
from rag_platform.modules.knowledge.contracts import (
    KnowledgeRepository,
    RetrievalTraceRecord,
    SearchHit,
)
from rag_platform.modules.model_runtime.contracts import EmbeddingRequest, ModelRuntime
from rag_platform.modules.ports import Clock, IdGenerator


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: tuple[SearchHit, ...]
    trace: RetrievalTraceRecord


class AuthorizedRetrieval:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        models: ModelRuntime,
        embedding_model_id: str,
        trace_ids: IdGenerator[TraceId],
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._models = models
        self._embedding_model_id = embedding_model_id
        self._trace_ids = trace_ids
        self._clock = clock

    def retrieve(
        self,
        context: AuthorizationContext,
        *,
        query: str,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        top_k: int,
    ) -> RetrievalResult:
        if not query.strip() or not knowledge_base_ids or not 1 <= top_k <= 1000:
            raise ValueError("invalid retrieval request")
        if len(set(knowledge_base_ids)) != len(knowledge_base_ids):
            raise ValueError("knowledge base ids must be unique")
        for knowledge_base_id in knowledge_base_ids:
            CorePolicies.require_knowledge_base(context, knowledge_base_id)
            if self._repository.get_knowledge_base(context, knowledge_base_id) is None:
                raise ResourceNotFound("knowledge base not found")
        embedded = self._models.embed(EmbeddingRequest(self._embedding_model_id, (query,)))
        hits = self._repository.search(context, knowledge_base_ids, embedded.vectors[0], top_k)
        trace = RetrievalTraceRecord(
            self._trace_ids.new(),
            context.tenant_id,
            knowledge_base_ids,
            hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "success" if hits else "no_evidence",
            len(hits),
            tuple(hit.chunk_id for hit in hits),
            True,
            self._now(),
        )
        self._repository.save_trace(trace)
        return RetrievalResult(hits, trace)

    def _now(self) -> datetime:
        now = self._clock.now()
        if not isinstance(now, datetime):
            raise TypeError("clock returned an invalid timestamp")
        return now
