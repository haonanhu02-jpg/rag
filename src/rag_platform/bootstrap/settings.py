"""Environment configuration validated at composition roots."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    log_level: str
    object_store_root: str = ".rag-objects"
    max_upload_bytes: int = 10 * 1024 * 1024
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "rag-chunks-v1"
    rag_max_context_characters: int = 12_000
    rag_minimum_evidence_score: float = 0.0
    rag_model_timeout_seconds: float = 30.0
    rag_max_input_tokens: int = 16_000
    rag_max_output_tokens: int = 2_000
    rag_max_cost_microunits: int = 1_000_000

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.environ.get("RAG_ENVIRONMENT", "development")
        database_url = os.environ.get(
            "RAG_DATABASE_URL", "postgresql+psycopg://rag:rag@localhost:5432/rag"
        )
        log_level = os.environ.get("RAG_LOG_LEVEL", "INFO").upper()
        object_store_root = os.environ.get("RAG_OBJECT_STORE_ROOT", ".rag-objects")
        elasticsearch_url = os.environ.get("RAG_ELASTICSEARCH_URL", "http://localhost:9200")
        elasticsearch_index = os.environ.get("RAG_ELASTICSEARCH_INDEX", "rag-chunks-v1")
        try:
            max_upload_bytes = int(os.environ.get("RAG_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
            rag_max_context_characters = int(
                os.environ.get("RAG_MAX_CONTEXT_CHARACTERS", "12000")
            )
            rag_minimum_evidence_score = float(
                os.environ.get("RAG_MINIMUM_EVIDENCE_SCORE", "0")
            )
            rag_model_timeout_seconds = float(
                os.environ.get("RAG_MODEL_TIMEOUT_SECONDS", "30")
            )
            rag_max_input_tokens = int(os.environ.get("RAG_MAX_INPUT_TOKENS", "16000"))
            rag_max_output_tokens = int(os.environ.get("RAG_MAX_OUTPUT_TOKENS", "2000"))
            rag_max_cost_microunits = int(
                os.environ.get("RAG_MAX_COST_MICROUNITS", "1000000")
            )
        except ValueError as exc:
            raise ConfigurationError("numeric RAG settings are invalid") from exc
        settings = cls(
            environment,
            database_url,
            log_level,
            object_store_root,
            max_upload_bytes,
            elasticsearch_url,
            elasticsearch_index,
            rag_max_context_characters,
            rag_minimum_evidence_score,
            rag_model_timeout_seconds,
            rag_max_input_tokens,
            rag_max_output_tokens,
            rag_max_cost_microunits,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ConfigurationError("RAG_ENVIRONMENT is invalid")
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ConfigurationError("RAG_DATABASE_URL must use PostgreSQL")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("RAG_LOG_LEVEL is invalid")
        if not self.object_store_root.strip():
            raise ConfigurationError("RAG_OBJECT_STORE_ROOT must not be empty")
        if not 1 <= self.max_upload_bytes <= 100 * 1024 * 1024:
            raise ConfigurationError("RAG_MAX_UPLOAD_BYTES is outside the safe range")
        if not self.elasticsearch_url.startswith(("http://", "https://")):
            raise ConfigurationError("RAG_ELASTICSEARCH_URL must use HTTP or HTTPS")
        if not self.elasticsearch_index.strip():
            raise ConfigurationError("RAG_ELASTICSEARCH_INDEX must not be empty")
        if self.rag_max_context_characters < 1:
            raise ConfigurationError("RAG_MAX_CONTEXT_CHARACTERS must be positive")
        if not 0 <= self.rag_minimum_evidence_score <= 1:
            raise ConfigurationError("RAG_MINIMUM_EVIDENCE_SCORE must be within [0, 1]")
        if self.rag_model_timeout_seconds <= 0:
            raise ConfigurationError("RAG_MODEL_TIMEOUT_SECONDS must be positive")
        if min(
            self.rag_max_input_tokens,
            self.rag_max_output_tokens,
            self.rag_max_cost_microunits,
        ) < 1:
            raise ConfigurationError("RAG generation budgets must be positive")
