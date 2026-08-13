"""Composition of the independently runnable R2 knowledge vertical slice."""

from __future__ import annotations

from pathlib import Path

from rag_platform.adapters.outbound.object_store import FileObjectStore
from rag_platform.adapters.outbound.postgres import (
    PostgresKnowledgeRepository,
    create_postgres_engine,
)
from rag_platform.adapters.outbound.system import SystemClock, UuidGenerator
from rag_platform.bootstrap.settings import Settings
from rag_platform.domain.identifiers import KnowledgeBaseId, TraceId
from rag_platform.modules.grounded_rag import GroundedRag
from rag_platform.modules.knowledge import KnowledgeService, PlainTextDocumentCompiler
from rag_platform.modules.model_runtime import FakeModelRuntime
from rag_platform.modules.model_runtime.contracts import ModelKind, ModelRegistration
from rag_platform.modules.retrieval import AuthorizedRetrieval
from rag_platform.orchestration.ingestion_graph import IngestionGraph

EMBEDDING_MODEL_ID = "r2-deterministic-embedding"
CHAT_MODEL_ID = "r2-deterministic-chat"


class R2Runtime:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_postgres_engine(settings.database_url)
        self.repository = PostgresKnowledgeRepository(self.engine)
        self.object_store = FileObjectStore(Path(settings.object_store_root))
        self.clock = SystemClock()
        self.models = FakeModelRuntime(
            (
                ModelRegistration(
                    EMBEDDING_MODEL_ID,
                    "deterministic",
                    "sha256-8",
                    ModelKind.EMBEDDING,
                ),
                ModelRegistration(
                    CHAT_MODEL_ID,
                    "deterministic",
                    "grounded-template-v1",
                    ModelKind.CHAT,
                ),
            ),
            chat_response="根据授权知识库中的证据, 答案见引用 [1]。",
        )
        self.knowledge = KnowledgeService(
            repository=self.repository,
            object_store=self.object_store,
            knowledge_base_ids=UuidGenerator(KnowledgeBaseId),
            clock=self.clock,
            max_upload_bytes=settings.max_upload_bytes,
        )
        self.ingestion = IngestionGraph(
            repository=self.repository,
            object_store=self.object_store,
            compiler=PlainTextDocumentCompiler(),
            models=self.models,
            embedding_model_id=EMBEDDING_MODEL_ID,
            clock=self.clock,
        )
        self.retrieval = AuthorizedRetrieval(
            repository=self.repository,
            models=self.models,
            embedding_model_id=EMBEDDING_MODEL_ID,
            trace_ids=UuidGenerator(TraceId),
            clock=self.clock,
        )
        self.grounded_rag = GroundedRag(
            retrieval=self.retrieval,
            models=self.models,
            chat_model_id=CHAT_MODEL_ID,
        )

    def close(self) -> None:
        self.engine.dispose()
