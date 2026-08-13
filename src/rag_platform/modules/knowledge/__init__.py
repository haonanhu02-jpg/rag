"""Minimum knowledge ingestion contracts and use cases."""

from rag_platform.modules.knowledge.compiler import DocumentCompiler, PlainTextDocumentCompiler
from rag_platform.modules.knowledge.contracts import (
    CompiledBlock,
    CompiledChunk,
    CompiledDocument,
    IngestionJobRecord,
    IngestionSource,
    KnowledgeBaseRecord,
    KnowledgeRepository,
    SearchHit,
    UploadSubmission,
)
from rag_platform.modules.knowledge.service import KnowledgeService

__all__ = [
    "CompiledBlock",
    "CompiledChunk",
    "CompiledDocument",
    "DocumentCompiler",
    "IngestionJobRecord",
    "IngestionSource",
    "KnowledgeBaseRecord",
    "KnowledgeRepository",
    "KnowledgeService",
    "PlainTextDocumentCompiler",
    "SearchHit",
    "UploadSubmission",
]
