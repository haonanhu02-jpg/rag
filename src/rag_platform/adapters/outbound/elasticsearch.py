"""Elasticsearch 9 search projection with explicit BM25 and kNN channels."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from threading import Lock
from typing import Any, cast
from uuid import UUID

from elasticsearch import Elasticsearch, helpers

from rag_platform.domain.identifiers import (
    ChunkId,
    DocumentId,
    DocumentVersionId,
    IndexVersionId,
    KnowledgeBaseId,
    TenantId,
)
from rag_platform.modules.knowledge.contracts import (
    CompiledDocument,
    IngestionSource,
    SearchHit,
    StagedGeneration,
)
from rag_platform.modules.retrieval.contracts import (
    FilterExpression,
    FilterGroupOperator,
    FilterOperator,
    MetadataField,
    MetadataFilter,
    SearchDependencyError,
    SearchScope,
)
from rag_platform.modules.retrieval.query import detect_language

_FIELD_NAMES = {
    MetadataField.DOCUMENT_ID: "document_id",
    MetadataField.DOCUMENT_VERSION_ID: "document_version_id",
    MetadataField.MEDIA_TYPE: "media_type",
    MetadataField.LANGUAGE: "language",
    MetadataField.CREATED_AT: "created_at",
    MetadataField.HEADING_PATH: "heading_path",
    MetadataField.CONTAINS_TABLE: "contains_table",
    MetadataField.CONTAINS_IMAGE: "contains_image",
    MetadataField.CHUNK_STRATEGY_ID: "chunk_strategy_id",
}


class ElasticsearchSearchAdapter:
    def __init__(self, url: str, *, index_name: str, vector_dimensions: int = 8) -> None:
        self._client = Elasticsearch(url, request_timeout=10, retry_on_timeout=False)
        self._index = index_name
        self._vector_dimensions = vector_dimensions
        self._ensure_lock = Lock()

    def close(self) -> None:
        self._client.close()

    def ensure_index(self) -> None:
        try:
            with self._ensure_lock:
                if self._client.indices.exists(index=self._index):
                    return
                self._client.indices.create(
                    index=self._index,
                    mappings={
                        "dynamic": "strict",
                        "properties": {
                            "tenant_id": {"type": "keyword"},
                            "knowledge_base_id": {"type": "keyword"},
                            "document_id": {"type": "keyword"},
                            "document_version_id": {"type": "keyword"},
                            "chunk_id": {"type": "keyword"},
                            "index_version_id": {"type": "keyword"},
                            "content": {"type": "text"},
                            "embedding": {
                                "type": "dense_vector",
                                "dims": self._vector_dimensions,
                                "index": True,
                                "similarity": "cosine",
                            },
                            "media_type": {"type": "keyword"},
                            "language": {"type": "keyword"},
                            "created_at": {"type": "date"},
                            "heading_path": {"type": "keyword"},
                            "contains_table": {"type": "boolean"},
                            "contains_image": {"type": "boolean"},
                            "chunk_strategy_id": {"type": "keyword"},
                            "document_enabled": {"type": "boolean"},
                            "document_deleted": {"type": "boolean"},
                            "source": {"type": "object", "enabled": False},
                        },
                    },
                )
        except Exception as exc:
            raise SearchDependencyError("failed to initialize search index") from exc

    def project_document(
        self,
        source: IngestionSource,
        document: CompiledDocument,
        vectors: tuple[tuple[float, ...], ...],
        generation: StagedGeneration,
        now: datetime,
    ) -> None:
        if len(document.chunks) != len(vectors):
            raise ValueError("projection chunk/vector cardinality mismatch")
        self.ensure_index()
        actions: list[dict[str, object]] = []
        for chunk, vector in zip(document.chunks, vectors, strict=True):
            source_data = dict(chunk.source)
            kinds = set(source_data.get("block_kinds", "").split(","))
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self._index,
                    "_id": str(chunk.id),
                    "_source": {
                        "tenant_id": str(source.job.tenant_id),
                        "knowledge_base_id": str(source.job.knowledge_base_id),
                        "document_id": str(source.job.document_id),
                        "document_version_id": str(source.job.document_version_id),
                        "chunk_id": str(chunk.id),
                        "index_version_id": str(generation.index_version_id),
                        "content": chunk.text,
                        "embedding": list(vector),
                        "media_type": source.media_type,
                        "language": detect_language(chunk.text),
                        "created_at": now.isoformat(),
                        "heading_path": source_data.get("heading_path", ""),
                        "contains_table": "table" in kinds,
                        "contains_image": "image" in kinds,
                        "chunk_strategy_id": source.chunk_method,
                        "document_enabled": True,
                        "document_deleted": False,
                        "source": source_data,
                    },
                }
            )
        try:
            helpers.bulk(self._client, actions, refresh="wait_for")
        except Exception as exc:
            raise SearchDependencyError("failed to write search projection") from exc

    def full_text(self, scope: SearchScope) -> tuple[SearchHit, ...]:
        self.ensure_index()
        texts = tuple(dict.fromkeys(item.text for item in scope.variants))
        should: list[dict[str, object]] = []
        for value in texts:
            should.extend(
                (
                    {"match_phrase": {"content": {"query": value, "boost": 2.0}}},
                    {"match": {"content": {"query": value}}},
                )
            )
        if scope.keywords:
            should.append({"match": {"content": {"query": " ".join(scope.keywords)}}})
        query = {
            "bool": {
                "filter": self._filters(scope),
                "should": should,
                "minimum_should_match": 1,
            }
        }
        try:
            response = self._client.search(index=self._index, query=query, size=scope.top_k)
            return self._hits(response, "full_text")
        except Exception as exc:
            raise SearchDependencyError("BM25 search failed") from exc

    def vector(self, scope: SearchScope, query_vector: tuple[float, ...]) -> tuple[SearchHit, ...]:
        self.ensure_index()
        try:
            response = self._client.search(
                index=self._index,
                knn={
                    "field": "embedding",
                    "query_vector": list(query_vector),
                    "k": scope.top_k,
                    "num_candidates": min(max(scope.top_k * 4, 100), 10_000),
                    "filter": self._filters(scope),
                },
                size=scope.top_k,
            )
            return self._hits(response, "vector")
        except Exception as exc:
            raise SearchDependencyError("kNN search failed") from exc

    def _filters(self, scope: SearchScope) -> list[dict[str, object]]:
        hard: list[dict[str, object]] = [
            {"term": {"tenant_id": str(scope.tenant_id)}},
            {"terms": {"knowledge_base_id": [str(value) for value in scope.knowledge_base_ids]}},
            {"term": {"document_enabled": True}},
            {"term": {"document_deleted": False}},
        ]
        for expression in (scope.user_filter, scope.inferred_filter):
            if expression is not None:
                hard.append(compile_filter(expression))
        return hard

    @staticmethod
    def _hits(response: Any, channel: str) -> tuple[SearchHit, ...]:
        raw_hits = cast(list[Mapping[str, object]], response["hits"]["hits"])
        values: list[SearchHit] = []
        for rank, raw in enumerate(raw_hits, start=1):
            data = cast(Mapping[str, object], raw["_source"])
            raw_source = data.get("source", {})
            source = (
                {str(key): str(value) for key, value in raw_source.items()}
                if isinstance(raw_source, Mapping)
                else {}
            )
            raw_score = raw.get("_score")
            score = float(raw_score) if isinstance(raw_score, (float, int)) else 0.0
            values.append(
                SearchHit(
                    TenantId(UUID(str(data["tenant_id"]))),
                    KnowledgeBaseId(UUID(str(data["knowledge_base_id"]))),
                    DocumentId(UUID(str(data["document_id"]))),
                    DocumentVersionId(UUID(str(data["document_version_id"]))),
                    ChunkId(UUID(str(data["chunk_id"]))),
                    str(data["content"]),
                    source,
                    score,
                    rank,
                    full_text_score=score if channel == "full_text" else None,
                    vector_score=score if channel == "vector" else None,
                    full_text_rank=rank if channel == "full_text" else None,
                    vector_rank=rank if channel == "vector" else None,
                    index_version_id=IndexVersionId(UUID(str(data["index_version_id"]))),
                )
            )
        return tuple(values)


def compile_filter(expression: FilterExpression) -> dict[str, object]:
    if isinstance(expression, MetadataFilter):
        field = _FIELD_NAMES[expression.field]
        if expression.operator is FilterOperator.EQUALS:
            return {"term": {field: expression.value}}
        if expression.operator is FilterOperator.IN:
            return {"terms": {field: list(cast(tuple[object, ...], expression.value))}}
        operation = "gte" if expression.operator is FilterOperator.GREATER_THAN_OR_EQUAL else "lte"
        return {"range": {field: {operation: expression.value}}}
    compiled = [compile_filter(item) for item in expression.items]
    if expression.operator is FilterGroupOperator.AND:
        return {"bool": {"filter": compiled}}
    if expression.operator is FilterGroupOperator.OR:
        return {"bool": {"should": compiled, "minimum_should_match": 1}}
    return {"bool": {"must_not": compiled}}
