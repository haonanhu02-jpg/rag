from __future__ import annotations

from rag_platform.adapters.outbound.elasticsearch import compile_filter
from rag_platform.domain.identifiers import KnowledgeBaseId
from rag_platform.modules.model_runtime import FakeModelRuntime
from rag_platform.modules.model_runtime.contracts import ModelKind, ModelRegistration
from rag_platform.modules.retrieval.contracts import (
    FilterGroupOperator,
    FilterOperator,
    MetadataField,
    MetadataFilter,
    MetadataFilterGroup,
    RetrievalRequest,
    parse_filter_expression,
)
from rag_platform.modules.retrieval.query import QueryProcessor, normalize_query


def test_normalization_removes_control_and_canonicalizes_unicode() -> None:
    assert normalize_query("  \uff21\u200b\uff22\n query\x00 ") == "AB query"


def test_query_transform_failure_retains_canonical_and_local_keywords() -> None:
    models = FakeModelRuntime(
        (ModelRegistration("chat", "fake", "fake", ModelKind.CHAT),),
        chat_response="invalid structured output",
    )
    processor = QueryProcessor(models=models, transform_model_id="chat")
    request = RetrievalRequest(
        " Reset the relay ",
        (_kb(),),
        top_k=10,
        top_n=3,
        history=("Which alarm?",),
        target_languages=("zh",),
    )

    result = processor.process(request)

    assert result.canonical == "Reset the relay"
    assert [variant.kind for variant in result.variants] == ["canonical", "keyword"]
    assert result.provider_ids == ()


def test_structured_transform_adds_standalone_translation_and_keywords() -> None:
    models = FakeModelRuntime(
        (ModelRegistration("chat", "fake", "fake", ModelKind.CHAT),),
        structured_response={
            "standalone_question": "How is the relay reset?",
            "translations": [{"language": "zh", "text": "如何复位继电器\uff1f"}],
            "keywords": ["controller"],
        },
    )
    result = QueryProcessor(models=models, transform_model_id="chat").process(
        RetrievalRequest(
            "How?",
            (_kb(),),
            top_k=10,
            top_n=3,
            history=("Reset the relay",),
            target_languages=("zh",),
        )
    )

    assert [variant.kind for variant in result.variants] == [
        "canonical",
        "keyword",
        "rewrite",
        "translation",
    ]
    assert result.keywords[-1] == "controller"
    assert result.provider_ids == ("chat",)


def test_recursive_filter_parser_and_elasticsearch_compiler() -> None:
    expression = parse_filter_expression(
        {
            "operator": "and",
            "items": [
                {"field": "media_type", "operator": "in", "value": ["text/plain"]},
                {
                    "operator": "not",
                    "items": [
                        {
                            "field": "contains_image",
                            "operator": "equals",
                            "value": True,
                        }
                    ],
                },
            ],
        }
    )

    assert expression == MetadataFilterGroup(
        FilterGroupOperator.AND,
        (
            MetadataFilter(MetadataField.MEDIA_TYPE, FilterOperator.IN, ("text/plain",)),
            MetadataFilterGroup(
                FilterGroupOperator.NOT,
                (MetadataFilter(MetadataField.CONTAINS_IMAGE, FilterOperator.EQUALS, True),),
            ),
        ),
    )
    assert compile_filter(expression) == {
        "bool": {
            "filter": [
                {"terms": {"media_type": ["text/plain"]}},
                {"bool": {"must_not": [{"term": {"contains_image": True}}]}},
            ]
        }
    }


def test_security_fields_and_pathological_filter_trees_are_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="tenant_id"):
        parse_filter_expression({"field": "tenant_id", "operator": "equals", "value": "attacker"})
    deep: dict[str, object] = {
        "field": "media_type",
        "operator": "equals",
        "value": "text/plain",
    }
    for _ in range(9):
        deep = {"operator": "not", "items": [deep]}
    with pytest.raises(ValueError, match="too complex"):
        parse_filter_expression(deep)


def _kb() -> KnowledgeBaseId:
    from uuid import UUID

    return KnowledgeBaseId(UUID(int=1))
