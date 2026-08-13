"""Model runtime public interface."""

from rag_platform.modules.model_runtime.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    EmbeddingRequest,
    EmbeddingResult,
    ModelKind,
    ModelRegistration,
    ModelRuntime,
    ModelUsage,
    RerankedItem,
    RerankRequest,
    RerankResult,
)
from rag_platform.modules.model_runtime.fake import FakeModelRuntime

__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResult",
    "EmbeddingRequest",
    "EmbeddingResult",
    "FakeModelRuntime",
    "ModelKind",
    "ModelRegistration",
    "ModelRuntime",
    "ModelUsage",
    "RerankRequest",
    "RerankResult",
    "RerankedItem",
]
