"""LangGraph orchestration for the R2 ingestion state machine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from rag_platform.domain.entities import WorkStatus
from rag_platform.domain.identifiers import JobId
from rag_platform.modules.knowledge.compiler import DocumentCompiler
from rag_platform.modules.knowledge.contracts import (
    CompiledDocument,
    DocumentParseError,
    DocumentResourceLimit,
    IngestionJobRecord,
    IngestionSource,
    KnowledgeRepository,
    StagedGeneration,
)
from rag_platform.modules.model_runtime.contracts import EmbeddingRequest, ModelRuntime
from rag_platform.modules.ports import Clock, ObjectStore
from rag_platform.modules.retrieval.contracts import SearchProjection


class IngestionState(TypedDict, total=False):
    job_id: JobId
    source: NotRequired[IngestionSource]
    document: NotRequired[CompiledDocument]
    vectors: NotRequired[tuple[tuple[float, ...], ...]]
    generation: NotRequired[StagedGeneration]
    job: NotRequired[IngestionJobRecord]


class IngestionGraph:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        object_store: ObjectStore,
        compiler: DocumentCompiler,
        models: ModelRuntime,
        embedding_model_id: str,
        clock: Clock,
        search_projection: SearchProjection | None = None,
    ) -> None:
        self._repository = repository
        self._object_store = object_store
        self._compiler = compiler
        self._models = models
        self._embedding_model_id = embedding_model_id
        self._clock = clock
        self._search_projection = search_projection
        builder = StateGraph(IngestionState)
        builder.add_node("load", self._load)
        builder.add_node("compile", self._compile)
        builder.add_node("embed", self._embed)
        builder.add_node("stage", self._stage)
        builder.add_node("project", self._project)
        builder.add_node("validate", self._validate)
        builder.add_node("publish", self._publish)
        builder.add_edge(START, "load")
        builder.add_conditional_edges("load", self._after_load)
        builder.add_edge("compile", "embed")
        builder.add_edge("embed", "stage")
        builder.add_edge("stage", "project")
        builder.add_edge("project", "validate")
        builder.add_edge("validate", "publish")
        builder.add_edge("publish", END)
        self._graph = builder.compile()

    def run(self, job_id: JobId) -> IngestionJobRecord:
        try:
            state = cast(IngestionState, self._graph.invoke({"job_id": job_id}))
            return state["job"]
        except Exception as exc:
            code = "ingestion_failed"
            if isinstance(exc, DocumentParseError):
                code = exc.code
            elif isinstance(exc, DocumentResourceLimit):
                code = "parser_resource_limit"
            self._repository.fail_ingestion(
                job_id,
                code=code,
                message=str(exc)[:1000],
                now=self._now(),
            )
            raise

    def _load(self, state: IngestionState) -> IngestionState:
        source = self._repository.begin_ingestion(state["job_id"], self._now())
        if source.job.status is WorkStatus.SUCCEEDED:
            return {"source": source, "job": source.job}
        return {"source": source}

    @staticmethod
    def _after_load(state: IngestionState) -> Literal["compile", "__end__"]:
        return "__end__" if "job" in state else "compile"

    def _compile(self, state: IngestionState) -> IngestionState:
        source = state["source"]
        content = self._object_store.get(
            tenant_id=source.job.tenant_id,
            key=source.object_key,
        )
        if content is None:
            raise FileNotFoundError("source object is missing")
        document = self._compiler.compile(
            tenant_id=source.job.tenant_id,
            knowledge_base_id=source.job.knowledge_base_id,
            document_id=source.job.document_id,
            media_type=source.media_type,
            content=content,
            source_sha256=source.source_sha256,
            file_name=source.file_name,
            chunk_method=source.chunk_method,
        )
        return {"document": document}

    def _embed(self, state: IngestionState) -> IngestionState:
        document = state["document"]
        result = self._models.embed(
            EmbeddingRequest(
                self._embedding_model_id,
                tuple(chunk.text for chunk in document.chunks),
            )
        )
        return {"vectors": result.vectors}

    def _stage(self, state: IngestionState) -> IngestionState:
        value = self._repository.stage_generation(
            state["source"],
            state["document"],
            state["vectors"],
            self._embedding_model_id,
            self._now(),
        )
        return {"generation": value}

    def _validate(self, state: IngestionState) -> IngestionState:
        self._repository.validate_generation(state["generation"])
        return {}

    def _project(self, state: IngestionState) -> IngestionState:
        if self._search_projection is not None:
            self._search_projection.project_document(
                state["source"],
                state["document"],
                state["vectors"],
                state["generation"],
                self._now(),
            )
        return {}

    def _publish(self, state: IngestionState) -> IngestionState:
        job = self._repository.publish_generation(state["source"], state["generation"], self._now())
        if job.status is not WorkStatus.SUCCEEDED:
            raise RuntimeError("published ingestion did not succeed")
        return {"job": job}

    def _now(self) -> datetime:
        now = self._clock.now()
        if not isinstance(now, datetime):
            raise TypeError("clock returned an invalid timestamp")
        return now
