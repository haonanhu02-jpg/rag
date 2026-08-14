from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from rag_platform.adapters.outbound.elasticsearch import ElasticsearchSearchAdapter
from rag_platform.domain.entities import WorkStatus
from rag_platform.domain.identifiers import (
    ActorId,
    ChunkId,
    DocumentId,
    DocumentVersionId,
    IndexVersionId,
    JobId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)
from rag_platform.modules.knowledge.contracts import (
    CompiledChunk,
    CompiledDocument,
    IngestionJobRecord,
    IngestionSource,
    StagedGeneration,
)
from rag_platform.modules.retrieval.contracts import QueryVariant, QueryVariantKind, SearchScope


def test_real_elasticsearch_bm25_knn_and_hard_scope() -> None:
    url = os.environ.get("RAG_TEST_ELASTICSEARCH_URL")
    if url is None:
        pytest.skip("RAG_TEST_ELASTICSEARCH_URL is required for real search integration")
    adapter = ElasticsearchSearchAdapter(url, index_name=f"rag-r4-{uuid4().hex}")
    tenant = TenantId(UUID(int=1))
    other_tenant = TenantId(UUID(int=2))
    kb = KnowledgeBaseId(UUID(int=3))
    try:
        _project(adapter, tenant, kb, 10, "quasar relay recovery", (1.0,) * 8)
        _project(adapter, other_tenant, kb, 20, "quasar secret", (1.0,) * 8)
        scope = SearchScope(
            tenant,
            ActorId(UUID(int=4)),
            frozenset({"owner"}),
            (kb,),
            (QueryVariant("quasar relay", QueryVariantKind.CANONICAL, "en"),),
            ("quasar", "relay"),
            10,
        )

        lexical = adapter.full_text(scope)
        vector = adapter.vector(scope, (1.0,) * 8)

        assert [item.tenant_id for item in lexical] == [tenant]
        assert [item.tenant_id for item in vector] == [tenant]
        assert lexical[0].full_text_score is not None
        assert vector[0].vector_score is not None
    finally:
        adapter.close()


def _project(
    adapter: ElasticsearchSearchAdapter,
    tenant: TenantId,
    kb: KnowledgeBaseId,
    seed: int,
    content: str,
    vector: tuple[float, ...],
) -> None:
    now = datetime.now(UTC)
    document_id = DocumentId(UUID(int=seed + 1))
    version_id = DocumentVersionId(UUID(int=seed + 2))
    job = IngestionJobRecord(
        JobId(UUID(int=seed + 3)),
        tenant,
        kb,
        document_id,
        version_id,
        ActorId(UUID(int=seed + 4)),
        f"key-{seed}",
        TraceId(UUID(int=seed + 5)),
        WorkStatus.RUNNING,
        0.5,
        now,
        now,
    )
    source = IngestionSource(job, "manual.txt", "text/plain", "key", "a" * 64)
    document = CompiledDocument(
        (),
        (CompiledChunk(ChunkId(UUID(int=seed + 6)), 0, content, {"block_kinds": "paragraph"}),),
        "b" * 64,
        "test",
    )
    generation = StagedGeneration(IndexVersionId(UUID(int=seed + 7)), 1, 1, 8)
    adapter.project_document(source, document, (vector,), generation, now)
