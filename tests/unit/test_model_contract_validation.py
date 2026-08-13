from __future__ import annotations

import pytest

from rag_platform.modules.model_runtime import (
    ChatMessage,
    ChatRequest,
    EmbeddingRequest,
    ModelKind,
    ModelRegistration,
    RerankRequest,
)
from rag_platform.modules.model_runtime.contracts import InvocationPolicy, ModelUsage


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ModelRegistration("", "p", "m", ModelKind.CHAT),
        lambda: ModelRegistration("id", "p", "m", ModelKind.CHAT, -1),
        lambda: ChatMessage("invalid", "text"),
        lambda: InvocationPolicy(0, 1),
        lambda: ChatRequest("chat", ()),
        lambda: EmbeddingRequest("embedding", ()),
        lambda: RerankRequest("reranker", "", ("doc",), 1),
        lambda: RerankRequest("reranker", "q", ("doc",), 2),
    ],
)
def test_invalid_model_contracts_are_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_model_request_maps_are_immutable_and_usage_totals() -> None:
    schema = {"type": "object"}
    metadata = {"trace": "1"}
    request = ChatRequest(
        "chat",
        (ChatMessage("user", "hello"),),
        structured_schema=schema,
        metadata=metadata,
    )
    schema["type"] = "changed"
    metadata["trace"] = "changed"
    assert request.structured_schema == {"type": "object"}
    assert request.metadata == {"trace": "1"}
    assert ModelUsage(2, 3).total_tokens == 5
