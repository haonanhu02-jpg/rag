"""LangChain-backed implementation of the provider-neutral ModelRuntime port."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TypeVar, cast

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from rag_platform.modules.model_runtime.contracts import (
    ChatRequest,
    ChatResult,
    ChatStreamChunk,
    EmbeddingRequest,
    EmbeddingResult,
    InvalidModelOutput,
    JsonValue,
    ModelKind,
    ModelRegistration,
    ModelRuntimeError,
    ModelTimeout,
    ModelUsage,
    RerankedItem,
    RerankRequest,
    RerankResult,
    UnknownModel,
)

T = TypeVar("T")


class LangChainModelRuntime:
    """Keep all LangChain values inside this adapter."""

    def __init__(
        self,
        registrations: tuple[ModelRegistration, ...],
        *,
        chat_models: Mapping[str, BaseChatModel] | None = None,
        embedding_models: Mapping[str, Embeddings] | None = None,
        rerankers: Mapping[str, Runnable[dict[str, object], object]] | None = None,
    ) -> None:
        self._registrations = {item.id: item for item in registrations}
        self._chat_models = dict(chat_models or {})
        self._embedding_models = dict(embedding_models or {})
        self._rerankers = dict(rerankers or {})

    def _registration(self, model_id: str, kind: ModelKind) -> ModelRegistration:
        registration = self._registrations.get(model_id)
        if registration is None or registration.kind is not kind or not registration.enabled:
            raise UnknownModel(f"no enabled {kind.value} model registered as {model_id}")
        return registration

    @staticmethod
    def _invoke(
        function: Callable[[], T], *, timeout_seconds: float, max_retries: int
    ) -> tuple[T, int, int]:
        started = time.perf_counter()
        for attempt in range(1, max_retries + 2):
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(function)
            try:
                value = future.result(timeout=timeout_seconds)
                executor.shutdown(wait=True)
                return value, attempt, round((time.perf_counter() - started) * 1000)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                if attempt > max_retries:
                    raise ModelTimeout("model invocation timed out") from exc
            except Exception as exc:
                executor.shutdown(wait=True)
                if attempt > max_retries:
                    raise ModelRuntimeError("model invocation failed") from exc
        raise AssertionError("retry loop exhausted unexpectedly")

    @staticmethod
    def _messages(request: ChatRequest) -> list[SystemMessage | HumanMessage | AIMessage]:
        converted: list[SystemMessage | HumanMessage | AIMessage] = []
        for message in request.messages:
            if message.role == "system":
                converted.append(SystemMessage(message.content))
            elif message.role == "assistant":
                converted.append(AIMessage(message.content))
            else:
                converted.append(HumanMessage(message.content))
        return converted

    @staticmethod
    def _usage(raw: object, registration: ModelRegistration) -> ModelUsage:
        metadata = getattr(raw, "usage_metadata", None) or {}
        input_tokens = int(metadata.get("input_tokens", 0))
        output_tokens = int(metadata.get("output_tokens", 0))
        cost = (
            input_tokens * registration.input_cost_per_million
            + output_tokens * registration.output_cost_per_million
        ) // 1_000_000
        return ModelUsage(input_tokens, output_tokens, cost)

    def chat(self, request: ChatRequest) -> ChatResult:
        registration = self._registration(request.model_id, ModelKind.CHAT)
        model = self._chat_models.get(request.model_id)
        if model is None:
            raise UnknownModel(f"chat adapter missing for {request.model_id}")
        messages = self._messages(request)
        runnable: Runnable[object, object]
        if request.structured_schema is not None:
            runnable = cast(
                Runnable[object, object],
                model.with_structured_output(dict(request.structured_schema)),
            )
        else:
            runnable = cast(Runnable[object, object], model)
        raw, attempts, duration_ms = self._invoke(
            lambda: runnable.invoke(
                messages,
                config={"metadata": dict(request.metadata), "run_name": request.model_id},
            ),
            timeout_seconds=request.policy.timeout_seconds,
            max_retries=request.policy.max_retries,
        )
        structured: Mapping[str, JsonValue] | None = None
        text = ""
        if request.structured_schema is not None:
            if not isinstance(raw, Mapping):
                raise InvalidModelOutput("structured model output must be a mapping")
            structured = cast(Mapping[str, JsonValue], dict(raw))
        elif isinstance(raw, AIMessage):
            text = raw.text
        else:
            raise InvalidModelOutput("chat model output must be an AIMessage")
        return ChatResult(
            request.model_id,
            text,
            structured,
            self._usage(raw, registration),
            attempts,
            duration_ms,
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[ChatStreamChunk]:
        registration = self._registration(request.model_id, ModelKind.CHAT)
        model = self._chat_models.get(request.model_id)
        if model is None:
            raise UnknownModel(f"chat adapter missing for {request.model_id}")
        if request.structured_schema is not None:
            raise InvalidModelOutput("structured model output cannot be streamed")
        messages = self._messages(request)
        started = time.perf_counter()
        iterator = iter(
            model.stream(
                messages,
                config={"metadata": dict(request.metadata), "run_name": request.model_id},
            )
        )
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            while True:
                remaining = request.policy.timeout_seconds - (time.perf_counter() - started)
                if remaining <= 0:
                    raise ModelTimeout("streaming model invocation timed out")
                future = executor.submit(_next_stream_value, iterator)
                try:
                    done, raw = future.result(timeout=remaining)
                except concurrent.futures.TimeoutError as exc:
                    future.cancel()
                    raise ModelTimeout("streaming model invocation timed out") from exc
                except Exception as exc:
                    raise ModelRuntimeError("streaming model invocation failed") from exc
                if done:
                    return
                if not isinstance(raw, (AIMessage, AIMessageChunk)):
                    raise InvalidModelOutput("streaming chat output must be an AI message")
                reason = raw.response_metadata.get("finish_reason")
                yield ChatStreamChunk(
                    request.model_id,
                    raw.text,
                    self._usage(raw, registration),
                    str(reason) if reason is not None else None,
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self._registration(request.model_id, ModelKind.EMBEDDING)
        model = self._embedding_models.get(request.model_id)
        if model is None:
            raise UnknownModel(f"embedding adapter missing for {request.model_id}")
        raw, attempts, duration_ms = self._invoke(
            lambda: model.embed_documents(list(request.texts)),
            timeout_seconds=request.policy.timeout_seconds,
            max_retries=request.policy.max_retries,
        )
        if len(raw) != len(request.texts) or any(not vector for vector in raw):
            raise InvalidModelOutput("embedding output has an invalid shape")
        usage = ModelUsage(input_tokens=sum(len(text.split()) for text in request.texts))
        return EmbeddingResult(
            request.model_id,
            tuple(tuple(float(value) for value in vector) for vector in raw),
            usage,
            attempts,
            duration_ms,
        )

    def rerank(self, request: RerankRequest) -> RerankResult:
        self._registration(request.model_id, ModelKind.RERANKER)
        reranker = self._rerankers.get(request.model_id)
        if reranker is None:
            raise UnknownModel(f"reranker adapter missing for {request.model_id}")
        raw, attempts, duration_ms = self._invoke(
            lambda: reranker.invoke(
                {
                    "query": request.query,
                    "documents": list(request.documents),
                    "top_n": request.top_n,
                }
            ),
            timeout_seconds=request.policy.timeout_seconds,
            max_retries=request.policy.max_retries,
        )
        if not isinstance(raw, Sequence):
            raise InvalidModelOutput("reranker output must be a sequence")
        try:
            converted: list[RerankedItem] = []
            for raw_item in raw[: request.top_n]:
                item = cast(Mapping[str, int | float | str], raw_item)
                converted.append(RerankedItem(int(item["index"]), float(item["score"])))
            items = tuple(converted)
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidModelOutput("reranker output is invalid") from exc
        return RerankResult(
            request.model_id,
            items,
            ModelUsage(),
            attempts,
            duration_ms,
        )


def _next_stream_value(iterator: Iterator[object]) -> tuple[bool, object | None]:
    try:
        return False, next(iterator)
    except StopIteration:
        return True, None
