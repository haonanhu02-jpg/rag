from __future__ import annotations

from collections.abc import Sequence

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from rag_platform.adapters.outbound.langchain_runtime import LangChainModelRuntime
from rag_platform.modules.model_runtime import (
    ChatMessage,
    ChatRequest,
    EmbeddingRequest,
    FakeModelRuntime,
    ModelKind,
    ModelRegistration,
    RerankRequest,
)
from rag_platform.modules.model_runtime.contracts import ModelRuntime


class DeterministicEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


REGISTRATIONS = (
    ModelRegistration("chat", "fake", "chat", ModelKind.CHAT),
    ModelRegistration("embedding", "fake", "embedding", ModelKind.EMBEDDING),
    ModelRegistration("reranker", "fake", "reranker", ModelKind.RERANKER),
)


def assert_model_runtime_contract(runtime: ModelRuntime) -> None:
    chat = runtime.chat(ChatRequest("chat", (ChatMessage("user", "hello world"),)))
    assert chat.model_id == "chat"
    assert chat.text
    assert chat.attempts == 1

    embedding = runtime.embed(EmbeddingRequest("embedding", ("alpha", "beta")))
    assert len(embedding.vectors) == 2
    assert all(embedding.vectors)

    reranked = runtime.rerank(
        RerankRequest("reranker", "alpha", ("beta", "alpha beta"), top_n=1)
    )
    assert reranked.items[0].document_index == 1


def test_fake_model_runtime_contract() -> None:
    assert_model_runtime_contract(FakeModelRuntime(REGISTRATIONS))


def test_langchain_model_runtime_contract() -> None:
    chat = FakeMessagesListChatModel(responses=[AIMessage("hello")])

    def rerank(values: dict[str, object]) -> Sequence[dict[str, float | int]]:
        documents = values["documents"]
        assert isinstance(documents, list)
        return [{"index": 1, "score": 1.0}, {"index": 0, "score": 0.0}]

    runtime = LangChainModelRuntime(
        REGISTRATIONS,
        chat_models={"chat": chat},
        embedding_models={"embedding": DeterministicEmbeddings()},
        rerankers={"reranker": RunnableLambda(rerank)},
    )
    assert_model_runtime_contract(runtime)
