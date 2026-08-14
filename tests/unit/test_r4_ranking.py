from __future__ import annotations

from uuid import UUID

from rag_platform.domain.identifiers import (
    ChunkId,
    DocumentId,
    DocumentVersionId,
    KnowledgeBaseId,
    TenantId,
)
from rag_platform.modules.knowledge.contracts import SearchHit
from rag_platform.modules.retrieval.ranking import (
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_fusion,
    select_candidates,
)


def test_rrf_deduplicates_by_chunk_and_preserves_channel_explanations() -> None:
    a, b, c = _hit(1, 1.2), _hit(2, 1.0), _hit(3, 0.8)

    fused = reciprocal_rank_fusion((a, b), (b, c))

    assert [item.chunk_id for item in fused] == [b.chunk_id, a.chunk_id, c.chunk_id]
    assert fused[0].full_text_rank == 2
    assert fused[0].vector_rank == 1
    assert fused[0].full_text_score == 1.0
    assert fused[0].vector_score == 1.0
    assert fused[0].fusion_score == fused[0].score


def test_threshold_top_n_and_per_document_quota_are_deterministic() -> None:
    first = _hit(1, 0.9, document=1)
    second = _hit(2, 0.8, document=1)
    third = _hit(3, 0.7, document=2)

    selected = select_candidates(
        (first, second, third), top_n=3, threshold=0.5, per_document_quota=1
    )

    assert [item.chunk_id for item in selected] == [first.chunk_id, third.chunk_id]
    assert [item.rank for item in selected] == [1, 2]


def test_quality_metrics_have_known_values() -> None:
    ranked = ("a", "b", "c")
    relevant = frozenset({"b", "c"})
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert mean_reciprocal_rank(ranked, relevant) == 0.5
    assert 0 < ndcg_at_k(ranked, relevant, 3) < 1


def test_hybrid_union_recall_is_not_worse_than_either_channel() -> None:
    lexical = (_hit(1, 2.0),)
    vector = (_hit(2, 0.9),)
    relevant = frozenset({str(lexical[0].chunk_id), str(vector[0].chunk_id)})
    hybrid = reciprocal_rank_fusion(lexical, vector)

    hybrid_recall = recall_at_k(tuple(str(item.chunk_id) for item in hybrid), relevant, 2)
    lexical_recall = recall_at_k(tuple(str(item.chunk_id) for item in lexical), relevant, 2)
    vector_recall = recall_at_k(tuple(str(item.chunk_id) for item in vector), relevant, 2)
    assert hybrid_recall >= max(lexical_recall, vector_recall)


def _hit(value: int, score: float, *, document: int | None = None) -> SearchHit:
    return SearchHit(
        TenantId(UUID(int=1)),
        KnowledgeBaseId(UUID(int=2)),
        DocumentId(UUID(int=document or value + 10)),
        DocumentVersionId(UUID(int=value + 20)),
        ChunkId(UUID(int=value + 30)),
        f"content {value}",
        {},
        score,
        value,
    )
