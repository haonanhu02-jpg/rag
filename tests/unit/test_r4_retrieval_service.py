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
from rag_platform.modules.knowledge.contracts import (
    KnowledgeBaseRecord,
    RetrievalTraceRecord,
    SearchHit,
)
from rag_platform.modules.model_runtime import FakeModelRuntime
from rag_platform.modules.model_runtime.contracts import ModelKind, ModelRegistration
from rag_platform.modules.retrieval.contracts import (
    FilterOperator,
    MetadataField,
    MetadataFilter,
    RetrievalRequest,
    SearchDependencyError,
    SearchScope,
)
from rag_platform.modules.retrieval.query import QueryProcessor
from rag_platform.modules.retrieval.service import AuthorizedRetrieval


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 14, tzinfo=UTC)


class _Ids:
    def new(self) -> TraceId:
        return TraceId(UUID(int=90))


class _Metrics:
    def __init__(self) -> None:
        self.names: list[str] = []

    def increment(self, name: str) -> None:
        self.names.append(name)


class _Repository:
    def __init__(self, *, trace_failure: bool = False) -> None:
        self.trace_failure = trace_failure
        self.saved: list[RetrievalTraceRecord] = []

    def get_knowledge_base(
        self, context: AuthorizationContext, knowledge_base_id: KnowledgeBaseId
    ) -> KnowledgeBaseRecord | None:
        return KnowledgeBaseRecord(
            knowledge_base_id,
            context.tenant_id,
            context.actor_id,
            "kb",
            "",
            "tenant",
            "active",
            _Clock().now(),
            _Clock().now(),
        )

    def validate_search_hits(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        hits: tuple[SearchHit, ...],
    ) -> tuple[SearchHit, ...]:
        return tuple(
            hit
            for hit in hits
            if hit.tenant_id == context.tenant_id and hit.knowledge_base_id in knowledge_base_ids
        )

    def save_trace(self, value: RetrievalTraceRecord) -> None:
        if self.trace_failure:
            raise ConnectionError("trace unavailable")
        self.saved.append(value)


class _Search:
    def __init__(self, *, fail_full_text: bool = False, fail_vector: bool = False) -> None:
        self.fail_full_text = fail_full_text
        self.fail_vector = fail_vector
        self.scopes: list[SearchScope] = []

    def full_text(self, scope: SearchScope) -> tuple[SearchHit, ...]:
        self.scopes.append(scope)
        if self.fail_full_text:
            raise SearchDependencyError("lexical down")
        return (_hit(),)

    def vector(self, scope: SearchScope, query_vector: tuple[float, ...]) -> tuple[SearchHit, ...]:
        del query_vector
        self.scopes.append(scope)
        if self.fail_vector:
            raise SearchDependencyError("vector down")
        return (_hit(),)


def test_single_channel_failure_degrades_without_disguising_a_total_failure() -> None:
    repository = _Repository()
    service, _ = _service(repository, _Search(fail_vector=True))

    result = service.retrieve(_context(), request=_request())

    assert result.hits
    assert any(
        event["stage"] == "vector"
        and cast(dict[str, object], event["attributes"])["error"] == "SearchDependencyError"
        for event in result.trace.events
    )


def test_total_search_failure_is_not_reported_as_no_evidence() -> None:
    repository = _Repository()
    service, _ = _service(repository, _Search(fail_full_text=True, fail_vector=True))

    with pytest.raises(SearchDependencyError):
        service.retrieve(_context(), request=_request())

    assert repository.saved[0].status == "failed"
    assert repository.saved[0].error_code == "search_dependency_failed"


def test_trace_write_failure_does_not_block_authorized_results() -> None:
    repository = _Repository(trace_failure=True)
    service, metrics = _service(repository, _Search())

    result = service.retrieve(_context(), request=_request())

    assert result.hits
    assert metrics.names == ["retrieval_trace_write_failed"]


def test_fallback_never_changes_hard_scope_or_user_filters() -> None:
    search = _EmptySearch()
    service, _ = _service(_Repository(), search)
    user = MetadataFilter(MetadataField.MEDIA_TYPE, FilterOperator.EQUALS, "text/plain")
    inferred = MetadataFilter(MetadataField.LANGUAGE, FilterOperator.EQUALS, "en")

    result = service.retrieve(
        _context(),
        request=RetrievalRequest(
            "missing",
            (_kb(),),
            top_k=4,
            top_n=1,
            user_filter=user,
            inferred_filter=inferred,
        ),
    )

    assert result.empty_reason == "no_match"
    assert len(search.scopes) == 8
    assert all(scope.tenant_id == _tenant() for scope in search.scopes)
    assert all(scope.knowledge_base_ids == (_kb(),) for scope in search.scopes)
    assert all(scope.user_filter == user for scope in search.scopes)
    assert any(scope.inferred_filter is None for scope in search.scopes)
    assert len(result.trace.fallback_steps) == 4


class _EmptySearch(_Search):
    def full_text(self, scope: SearchScope) -> tuple[SearchHit, ...]:
        self.scopes.append(scope)
        return ()

    def vector(self, scope: SearchScope, query_vector: tuple[float, ...]) -> tuple[SearchHit, ...]:
        del query_vector
        self.scopes.append(scope)
        return ()


def _service(repository: _Repository, search: _Search) -> tuple[AuthorizedRetrieval, _Metrics]:
    models = FakeModelRuntime(
        (
            ModelRegistration("embedding", "fake", "fake", ModelKind.EMBEDDING),
            ModelRegistration("reranker", "fake", "fake", ModelKind.RERANKER),
            ModelRegistration("chat", "fake", "fake", ModelKind.CHAT),
        )
    )
    metrics = _Metrics()
    return (
        AuthorizedRetrieval(
            repository=cast(Any, repository),
            search=search,
            models=models,
            embedding_model_id="embedding",
            reranker_model_id="reranker",
            query_processor=QueryProcessor(models=models, transform_model_id="chat"),
            trace_ids=_Ids(),
            clock=_Clock(),
            metrics=metrics,
        ),
        metrics,
    )


def _context() -> AuthorizationContext:
    return AuthorizationContext.from_principal(
        TrustedPrincipal(_tenant(), ActorId(UUID(int=2)), frozenset({"owner"}))
    )


def _request() -> RetrievalRequest:
    return RetrievalRequest("relay", (_kb(),), top_k=4, top_n=1)


def _tenant() -> TenantId:
    return TenantId(UUID(int=1))


def _kb() -> KnowledgeBaseId:
    return KnowledgeBaseId(UUID(int=3))


def _hit() -> SearchHit:
    return SearchHit(
        _tenant(),
        _kb(),
        DocumentId(UUID(int=4)),
        DocumentVersionId(UUID(int=5)),
        ChunkId(UUID(int=6)),
        "relay reset evidence",
        {},
        1.0,
        1,
    )
