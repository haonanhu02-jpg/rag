"""Retrieve, generate from bounded evidence, and return source-bound citations."""

from __future__ import annotations

from dataclasses import dataclass

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import (
    ChunkId,
    DocumentId,
    DocumentVersionId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)
from rag_platform.modules.knowledge.contracts import SearchHit
from rag_platform.modules.model_runtime.contracts import (
    ChatMessage,
    ChatRequest,
    ModelRuntime,
)
from rag_platform.modules.retrieval.contracts import FilterExpression, RetrievalRequest
from rag_platform.modules.retrieval.service import AuthorizedRetrieval

FIXED_RAG_PROMPT_VERSION = "fixed-rag-v1"
NO_EVIDENCE_ANSWER = "未检索到可用于回答该问题的授权证据。"


@dataclass(frozen=True, slots=True)
class RagCitation:
    tenant_id: TenantId
    knowledge_base_id: KnowledgeBaseId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    chunk_id: ChunkId
    quote: str
    source: dict[str, str]


@dataclass(frozen=True, slots=True)
class FixedRagAnswer:
    status: str
    answer: str
    citations: tuple[RagCitation, ...]
    trace_id: TraceId
    prompt_version: str
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class GroundedRag:
    def __init__(
        self,
        *,
        retrieval: AuthorizedRetrieval,
        models: ModelRuntime,
        chat_model_id: str,
        max_context_characters: int = 12_000,
    ) -> None:
        self._retrieval = retrieval
        self._models = models
        self._chat_model_id = chat_model_id
        self._max_context_characters = max_context_characters

    def answer(
        self,
        context: AuthorizationContext,
        *,
        question: str,
        knowledge_base_ids: tuple[KnowledgeBaseId, ...],
        top_k: int = 20,
        top_n: int = 5,
        history: tuple[str, ...] = (),
        target_languages: tuple[str, ...] = (),
        user_filter: FilterExpression | None = None,
        request_id: str | None = None,
    ) -> FixedRagAnswer:
        if not 1 <= top_n <= min(top_k, 50):
            raise ValueError("top_n must be within top_k")
        retrieval = self._retrieval.retrieve(
            context,
            request=RetrievalRequest(
                question,
                knowledge_base_ids,
                top_k,
                top_n,
                history,
                target_languages,
                user_filter,
                None,
                request_id,
            ),
        )
        if not retrieval.hits:
            return FixedRagAnswer(
                "no_evidence",
                NO_EVIDENCE_ANSWER,
                (),
                retrieval.trace.id,
                FIXED_RAG_PROMPT_VERSION,
            )
        selected: list[tuple[SearchHit, str]] = []
        consumed = 0
        for hit in retrieval.hits:
            marker = f"[{len(selected) + 1}] {hit.content}\n"
            if selected and consumed + len(marker) > self._max_context_characters:
                break
            selected.append((hit, marker))
            consumed += len(marker)
        context_text = "".join(marker for _, marker in selected)
        generated = self._models.chat(
            ChatRequest(
                self._chat_model_id,
                (
                    ChatMessage(
                        "system",
                        "你是企业知识库问答助手。只能依据证据回答, 并用 [1]、[2] 标注引用。",
                    ),
                    ChatMessage("user", f"问题: {question}\n\n证据:\n{context_text}"),
                ),
                metadata={"trace_id": str(retrieval.trace.id)},
            )
        )
        citations = tuple(
            RagCitation(
                hit.tenant_id,
                hit.knowledge_base_id,
                hit.document_id,
                hit.document_version_id,
                hit.chunk_id,
                hit.content,
                dict(hit.source),
            )
            for hit, _ in selected
        )
        return FixedRagAnswer(
            "answered",
            generated.text,
            citations,
            retrieval.trace.id,
            FIXED_RAG_PROMPT_VERSION,
            generated.model_id,
            generated.usage.input_tokens,
            generated.usage.output_tokens,
        )
