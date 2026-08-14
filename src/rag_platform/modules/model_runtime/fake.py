"""Deterministic model runtime for contract tests; not a quality backend."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping

from rag_platform.modules.model_runtime.contracts import (
    ChatRequest,
    ChatResult,
    ChatStreamChunk,
    EmbeddingRequest,
    EmbeddingResult,
    JsonValue,
    ModelKind,
    ModelRegistration,
    ModelUsage,
    RerankedItem,
    RerankRequest,
    RerankResult,
    UnknownModel,
)


class FakeModelRuntime:
    def __init__(
        self,
        registrations: tuple[ModelRegistration, ...],
        *,
        chat_response: str | None = None,
        structured_response: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self._registrations = {registration.id: registration for registration in registrations}
        self._chat_response = chat_response
        self._structured_response = structured_response
        self.chat_requests: list[ChatRequest] = []
        self.embedding_requests: list[EmbeddingRequest] = []

    def _require(self, model_id: str, kind: ModelKind) -> ModelRegistration:
        registration = self._registrations.get(model_id)
        if registration is None or registration.kind is not kind or not registration.enabled:
            raise UnknownModel(f"no enabled {kind.value} model registered as {model_id}")
        return registration

    def chat(self, request: ChatRequest) -> ChatResult:
        self._require(request.model_id, ModelKind.CHAT)
        self.chat_requests.append(request)
        text = self._chat_response or request.messages[-1].content
        structured: dict[str, JsonValue] | None = None
        if request.structured_schema is not None:
            structured = dict(self._structured_response or {"text": text})
        tokens = sum(len(message.content.split()) for message in request.messages)
        return ChatResult(
            request.model_id,
            text,
            structured,
            ModelUsage(input_tokens=tokens, output_tokens=len(text.split())),
            attempts=1,
            duration_ms=0,
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[ChatStreamChunk]:
        result = self.chat(request)
        if result.structured is not None:
            raise ValueError("structured fake responses cannot be streamed")
        parts = re.findall(r"\S+\s*", result.text)
        for part in parts:
            yield ChatStreamChunk(result.model_id, part)
        yield ChatStreamChunk(result.model_id, "", result.usage, "stop")

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self._require(request.model_id, ModelKind.EMBEDDING)
        self.embedding_requests.append(request)
        vectors = tuple(self._vector(text) for text in request.texts)
        return EmbeddingResult(
            request.model_id,
            vectors,
            ModelUsage(input_tokens=sum(len(text.split()) for text in request.texts)),
            attempts=1,
            duration_ms=0,
        )

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(round(byte / 255, 6) for byte in digest[:8])

    def rerank(self, request: RerankRequest) -> RerankResult:
        self._require(request.model_id, ModelKind.RERANKER)
        query_terms = set(request.query.casefold().split())
        ranked = sorted(
            (
                RerankedItem(index, float(len(query_terms & set(document.casefold().split()))))
                for index, document in enumerate(request.documents)
            ),
            key=lambda item: (-item.score, item.document_index),
        )[: request.top_n]
        return RerankResult(
            request.model_id,
            tuple(ranked),
            ModelUsage(input_tokens=sum(len(document.split()) for document in request.documents)),
            attempts=1,
            duration_ms=0,
        )
