"""Deterministic retrieval fusion, reranking, filtering, and evaluation metrics."""

from __future__ import annotations

import math
from dataclasses import replace

from rag_platform.modules.knowledge.contracts import SearchHit
from rag_platform.modules.model_runtime.contracts import ModelRuntime, RerankRequest


def reciprocal_rank_fusion(
    full_text: tuple[SearchHit, ...],
    vector: tuple[SearchHit, ...],
    *,
    rank_constant: int = 60,
) -> tuple[SearchHit, ...]:
    if rank_constant < 1:
        raise ValueError("rank_constant must be positive")
    values: dict[object, SearchHit] = {}
    scores: dict[object, float] = {}
    for channel, hits in (("full_text", full_text), ("vector", vector)):
        for rank, hit in enumerate(hits, start=1):
            key = hit.chunk_id
            values.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank_constant + rank)
            current = values[key]
            if channel == "full_text":
                values[key] = replace(current, full_text_score=hit.score, full_text_rank=rank)
            else:
                values[key] = replace(current, vector_score=hit.score, vector_rank=rank)
    ordered = sorted(values.values(), key=lambda hit: (-scores[hit.chunk_id], str(hit.chunk_id)))
    return tuple(
        replace(hit, score=scores[hit.chunk_id], fusion_score=scores[hit.chunk_id], rank=rank)
        for rank, hit in enumerate(ordered, start=1)
    )


def rerank_candidates(
    models: ModelRuntime,
    *,
    model_id: str,
    query: str,
    candidates: tuple[SearchHit, ...],
) -> tuple[tuple[SearchHit, ...], str | None]:
    if not candidates:
        return (), None
    try:
        result = models.rerank(
            RerankRequest(
                model_id, query, tuple(item.content for item in candidates), len(candidates)
            )
        )
        by_index = {item.document_index: item.score for item in result.items}
        if not by_index or any(index >= len(candidates) for index in by_index):
            raise ValueError("reranker returned invalid indices")
        ordered = sorted(
            (candidates[index] for index in by_index),
            key=lambda item: (-by_index[candidates.index(item)], item.rank, str(item.chunk_id)),
        )
        return (
            tuple(
                replace(
                    item,
                    score=by_index[candidates.index(item)],
                    rerank_score=by_index[candidates.index(item)],
                    rerank_rank=rank,
                    rank=rank,
                )
                for rank, item in enumerate(ordered, start=1)
            ),
            None,
        )
    except Exception as exc:
        return candidates, f"reranker_error:{type(exc).__name__}"


def select_candidates(
    candidates: tuple[SearchHit, ...],
    *,
    top_n: int,
    threshold: float,
    per_document_quota: int,
) -> tuple[SearchHit, ...]:
    counts: dict[object, int] = {}
    selected: list[SearchHit] = []
    for candidate in candidates:
        if candidate.score < threshold:
            continue
        count = counts.get(candidate.document_id, 0)
        if count >= per_document_quota:
            continue
        counts[candidate.document_id] = count + 1
        selected.append(replace(candidate, rank=len(selected) + 1))
        if len(selected) == top_n:
            break
    return tuple(selected)


def recall_at_k(ranked: tuple[str, ...], relevant: frozenset[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mean_reciprocal_rank(ranked: tuple[str, ...], relevant: frozenset[str]) -> float:
    return next((1.0 / rank for rank, item in enumerate(ranked, 1) if item in relevant), 0.0)


def ndcg_at_k(ranked: tuple[str, ...], relevant: frozenset[str], k: int) -> float:
    actual = sum(
        1.0 / math.log2(rank + 1) for rank, item in enumerate(ranked[:k], 1) if item in relevant
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return actual / ideal if ideal else 0.0
