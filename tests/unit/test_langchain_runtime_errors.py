from __future__ import annotations

import time

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from rag_platform.adapters.outbound.langchain_runtime import LangChainModelRuntime
from rag_platform.modules.model_runtime import (
    ChatMessage,
    ChatRequest,
    EmbeddingRequest,
    InvocationPolicy,
    ModelKind,
    ModelRegistration,
    RerankRequest,
)
from rag_platform.modules.model_runtime.contracts import (
    InvalidModelOutput,
    ModelRuntimeError,
    ModelTimeout,
    UnknownModel,
)


class InvalidEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return []

    def embed_query(self, text: str) -> list[float]:
        return []


REGISTRATIONS = (
    ModelRegistration("chat", "fake", "chat", ModelKind.CHAT, 1_000_000, 1_000_000),
    ModelRegistration("embedding", "fake", "embedding", ModelKind.EMBEDDING),
    ModelRegistration("reranker", "fake", "reranker", ModelKind.RERANKER),
)


def test_langchain_chat_converts_roles_and_usage() -> None:
    response = AIMessage(
        "ok", usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    )
    model = FakeMessagesListChatModel(responses=[response])
    runtime = LangChainModelRuntime(REGISTRATIONS, chat_models={"chat": model})
    result = runtime.chat(
        ChatRequest(
            "chat",
            (
                ChatMessage("system", "rules"),
                ChatMessage("assistant", "previous"),
                ChatMessage("user", "question"),
            ),
        )
    )
    assert result.text == "ok"
    assert result.usage.total_tokens == 5
    assert result.usage.cost_microunits == 5


def test_runtime_unknown_adapter_and_invalid_outputs() -> None:
    runtime = LangChainModelRuntime(REGISTRATIONS)
    with pytest.raises(UnknownModel, match="chat adapter"):
        runtime.chat(ChatRequest("chat", (ChatMessage("user", "q"),)))
    with pytest.raises(UnknownModel, match="embedding adapter"):
        runtime.embed(EmbeddingRequest("embedding", ("q",)))
    with pytest.raises(UnknownModel, match="reranker adapter"):
        runtime.rerank(RerankRequest("reranker", "q", ("d",), 1))
    with pytest.raises(UnknownModel, match="enabled chat"):
        runtime.chat(ChatRequest("missing", (ChatMessage("user", "q"),)))

    bad_embedding_runtime = LangChainModelRuntime(
        REGISTRATIONS, embedding_models={"embedding": InvalidEmbeddings()}
    )
    with pytest.raises(InvalidModelOutput, match="shape"):
        bad_embedding_runtime.embed(EmbeddingRequest("embedding", ("q",)))

    bad_reranker = LangChainModelRuntime(
        REGISTRATIONS,
        rerankers={"reranker": RunnableLambda(lambda _: "not-a-sequence")},
    )
    with pytest.raises(InvalidModelOutput, match="invalid"):
        bad_reranker.rerank(RerankRequest("reranker", "q", ("d",), 1))


def test_invoke_retries_dependency_failure() -> None:
    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return "ok"

    assert LangChainModelRuntime._invoke(flaky, timeout_seconds=1, max_retries=1)[0] == "ok"
    with pytest.raises(ModelRuntimeError):
        LangChainModelRuntime._invoke(
            lambda: (_ for _ in ()).throw(ConnectionError("down")),
            timeout_seconds=1,
            max_retries=0,
        )


def test_invoke_times_out() -> None:
    def slow() -> None:
        time.sleep(0.05)

    with pytest.raises(ModelTimeout):
        LangChainModelRuntime._invoke(slow, timeout_seconds=0.001, max_retries=0)


def test_stream_contract_rejects_structured_missing_and_timed_out_models() -> None:
    model = FakeMessagesListChatModel(responses=[AIMessage("slow")], sleep=0.05)
    runtime = LangChainModelRuntime(REGISTRATIONS, chat_models={"chat": model})
    with pytest.raises(InvalidModelOutput, match="structured"):
        tuple(
            runtime.stream_chat(
                ChatRequest(
                    "chat",
                    (ChatMessage("user", "q"),),
                    structured_schema={"type": "object"},
                )
            )
        )
    with pytest.raises(UnknownModel, match="adapter"):
        tuple(LangChainModelRuntime(REGISTRATIONS).stream_chat(
            ChatRequest("chat", (ChatMessage("user", "q"),))
        ))
    with pytest.raises(ModelTimeout, match="streaming"):
        tuple(
            runtime.stream_chat(
                ChatRequest(
                    "chat",
                    (ChatMessage("user", "q"),),
                    policy=InvocationPolicy(timeout_seconds=0.001, max_retries=0),
                )
            )
        )
