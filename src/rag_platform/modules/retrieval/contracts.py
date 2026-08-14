"""Framework-neutral contracts for authorized hybrid retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import (
    ActorId,
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

type FilterScalar = str | int | float | bool


class MetadataField(StrEnum):
    DOCUMENT_ID = "document_id"
    DOCUMENT_VERSION_ID = "document_version_id"
    MEDIA_TYPE = "media_type"
    LANGUAGE = "language"
    CREATED_AT = "created_at"
    HEADING_PATH = "heading_path"
    CONTAINS_TABLE = "contains_table"
    CONTAINS_IMAGE = "contains_image"
    CHUNK_STRATEGY_ID = "chunk_strategy_id"


class FilterOperator(StrEnum):
    EQUALS = "equals"
    IN = "in"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"


class FilterGroupOperator(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    field: MetadataField
    operator: FilterOperator
    value: FilterScalar | tuple[FilterScalar, ...]

    def __post_init__(self) -> None:
        collection = isinstance(self.value, tuple)
        if self.operator is FilterOperator.IN:
            if not collection or not self.value:
                raise ValueError("in filters require a non-empty collection")
        elif collection:
            raise ValueError("only in filters accept a collection")
        if (
            self.operator
            in {
                FilterOperator.GREATER_THAN_OR_EQUAL,
                FilterOperator.LESS_THAN_OR_EQUAL,
            }
            and self.field is not MetadataField.CREATED_AT
        ):
            raise ValueError("range operators are only valid for created_at")
        expected = _FIELD_TYPES[self.field]
        values = (
            cast(tuple[FilterScalar, ...], self.value)
            if collection
            else (cast(FilterScalar, self.value),)
        )
        if any(type(item) not in expected for item in values):
            raise ValueError(f"invalid value type for {self.field.value}")


@dataclass(frozen=True, slots=True)
class MetadataFilterGroup:
    operator: FilterGroupOperator
    items: tuple[FilterExpression, ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("filter groups require at least one child")
        if self.operator is FilterGroupOperator.NOT and len(self.items) != 1:
            raise ValueError("not filter groups require exactly one child")


type FilterExpression = MetadataFilter | MetadataFilterGroup

_FIELD_TYPES: dict[MetadataField, tuple[type[object], ...]] = {
    MetadataField.DOCUMENT_ID: (str,),
    MetadataField.DOCUMENT_VERSION_ID: (str,),
    MetadataField.MEDIA_TYPE: (str,),
    MetadataField.LANGUAGE: (str,),
    MetadataField.CREATED_AT: (str,),
    MetadataField.HEADING_PATH: (str,),
    MetadataField.CONTAINS_TABLE: (bool,),
    MetadataField.CONTAINS_IMAGE: (bool,),
    MetadataField.CHUNK_STRATEGY_ID: (str,),
}


class QueryVariantKind(StrEnum):
    CANONICAL = "canonical"
    REWRITE = "rewrite"
    TRANSLATION = "translation"
    KEYWORD = "keyword"


@dataclass(frozen=True, slots=True)
class QueryVariant:
    text: str
    kind: QueryVariantKind
    language: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("query variants must not be empty")


@dataclass(frozen=True, slots=True)
class ProcessedQuery:
    canonical: str
    language: str
    keywords: tuple[str, ...]
    variants: tuple[QueryVariant, ...]
    inferred_filter: FilterExpression | None = None
    provider_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    knowledge_base_ids: tuple[KnowledgeBaseId, ...]
    top_k: int = 100
    top_n: int = 10
    history: tuple[str, ...] = ()
    target_languages: tuple[str, ...] = ()
    user_filter: FilterExpression | None = None
    inferred_filter: FilterExpression | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.query.strip() or not self.knowledge_base_ids:
            raise ValueError("retrieval requires a query and knowledge bases")
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("knowledge base ids must be unique")
        if not 1 <= self.top_n <= self.top_k <= 1000:
            raise ValueError("top_n and top_k are invalid")
        if len(self.history) > 16 or len(self.target_languages) > 4:
            raise ValueError("query transformations exceed their bounded limits")
        if any(not value.strip() for value in self.target_languages):
            raise ValueError("target languages must not be empty")


@dataclass(frozen=True, slots=True)
class SearchScope:
    tenant_id: TenantId
    actor_id: ActorId
    roles: frozenset[str]
    knowledge_base_ids: tuple[KnowledgeBaseId, ...]
    variants: tuple[QueryVariant, ...]
    keywords: tuple[str, ...]
    top_k: int
    user_filter: FilterExpression | None = None
    inferred_filter: FilterExpression | None = None


class SearchDependencyError(ConnectionError):
    """Search infrastructure failed; this must never be reported as no evidence."""


class SearchReader(Protocol):
    def full_text(self, scope: SearchScope) -> tuple[SearchHit, ...]: ...

    def vector(
        self, scope: SearchScope, query_vector: tuple[float, ...]
    ) -> tuple[SearchHit, ...]: ...


class SearchProjection(Protocol):
    def project_document(
        self,
        source: IngestionSource,
        document: CompiledDocument,
        vectors: tuple[tuple[float, ...], ...],
        generation: StagedGeneration,
        now: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AuthorityCandidate:
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    chunk_id: ChunkId
    index_version_id: IndexVersionId | None


class RetrievalAuthority(Protocol):
    def validate_search_hits(
        self,
        context: AuthorizationContext,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        hits: tuple[SearchHit, ...],
    ) -> tuple[SearchHit, ...]: ...


class RetrievalMetrics(Protocol):
    def increment(self, name: str) -> None: ...


class NullRetrievalMetrics:
    def increment(self, name: str) -> None:
        del name


def parse_filter_expression(
    value: Mapping[str, object], *, max_depth: int = 8, max_nodes: int = 64
) -> FilterExpression:
    """Parse an untrusted recursive filter with strict fields and bounded complexity."""

    remaining = [max_nodes]

    def parse(raw: Mapping[str, object], depth: int) -> FilterExpression:
        remaining[0] -= 1
        if depth > max_depth or remaining[0] < 0:
            raise ValueError("filter expression is too complex")
        keys = set(raw)
        if "items" in raw:
            if keys != {"operator", "items"}:
                raise ValueError("filter group has unknown fields")
            items = raw["items"]
            if not isinstance(items, list):
                raise ValueError("filter group items must be a list")
            return MetadataFilterGroup(
                FilterGroupOperator(str(raw["operator"])),
                tuple(parse(_mapping(item), depth + 1) for item in items),
            )
        if keys != {"field", "operator", "value"}:
            raise ValueError("filter leaf has unknown fields")
        operator = FilterOperator(str(raw["operator"]))
        raw_value = raw["value"]
        scalar: FilterScalar | tuple[FilterScalar, ...]
        if isinstance(raw_value, list):
            scalar = tuple(_scalar(item) for item in raw_value)
        else:
            scalar = _scalar(raw_value)
        return MetadataFilter(MetadataField(str(raw["field"])), operator, scalar)

    return parse(value, 1)


def combine_filters(expressions: tuple[FilterExpression, ...]) -> FilterExpression | None:
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    return MetadataFilterGroup(FilterGroupOperator.AND, expressions)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("filter children must be objects")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("filter keys must be strings")
    return value


def _scalar(value: object) -> FilterScalar:
    if type(value) not in {str, int, float, bool}:
        raise ValueError("filter values must be scalar")
    return value  # type: ignore[return-value]
