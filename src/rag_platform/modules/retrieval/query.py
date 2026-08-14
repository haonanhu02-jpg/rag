"""Bounded, failure-safe query normalization and transformation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from rag_platform.modules.model_runtime.contracts import (
    ChatMessage,
    ChatRequest,
    InvocationPolicy,
    JsonValue,
    ModelRuntime,
)
from rag_platform.modules.retrieval.contracts import (
    ProcessedQuery,
    QueryVariant,
    QueryVariantKind,
    RetrievalRequest,
)

_WHITESPACE = re.compile(r"\s+")
_KEYWORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*|[\u3400-\u9fff]{2,}")


class QueryProcessor:
    def __init__(
        self,
        *,
        models: ModelRuntime,
        transform_model_id: str,
        max_characters: int = 8000,
        max_keywords: int = 16,
    ) -> None:
        self._models = models
        self._transform_model_id = transform_model_id
        self._max_characters = max_characters
        self._max_keywords = max_keywords

    def process(self, request: RetrievalRequest) -> ProcessedQuery:
        canonical = normalize_query(request.query, max_characters=self._max_characters)
        language = detect_language(canonical)
        keywords = extract_keywords(canonical, limit=self._max_keywords)
        variants: list[QueryVariant] = [
            QueryVariant(canonical, QueryVariantKind.CANONICAL, language)
        ]
        providers: list[str] = []
        if keywords:
            variants.append(
                QueryVariant(" ".join(keywords), QueryVariantKind.KEYWORD, language, "local")
            )
        if request.history or request.target_languages:
            try:
                transformed = self._transform(canonical, request.history, request.target_languages)
                rewrite = transformed.get("standalone_question")
                translations = transformed.get("translations")
                expanded = transformed.get("keywords")
                if (
                    not isinstance(rewrite, str)
                    or not isinstance(translations, list)
                    or not isinstance(expanded, list)
                ):
                    raise ValueError("query transformer output does not match its schema")
                if isinstance(rewrite, str):
                    self._append(variants, rewrite, QueryVariantKind.REWRITE, language)
                if isinstance(translations, list):
                    for item in translations[: len(request.target_languages)]:
                        if isinstance(item, Mapping):
                            text = item.get("text")
                            target = item.get("language")
                            if isinstance(text, str) and isinstance(target, str):
                                self._append(variants, text, QueryVariantKind.TRANSLATION, target)
                if isinstance(expanded, list):
                    for item in expanded[: self._max_keywords]:
                        if isinstance(item, str) and item.strip():
                            keywords = tuple(dict.fromkeys((*keywords, item.strip())))
                providers.append(self._transform_model_id)
            except Exception:
                # Canonical query is deliberately retained on timeout or malformed output.
                pass
        return ProcessedQuery(
            canonical,
            language,
            keywords[: self._max_keywords],
            tuple(variants),
            request.inferred_filter,
            tuple(providers),
        )

    def _transform(
        self, canonical: str, history: tuple[str, ...], languages: tuple[str, ...]
    ) -> Mapping[str, JsonValue]:
        schema: dict[str, JsonValue] = {
            "type": "object",
            "required": ["standalone_question", "translations", "keywords"],
            "properties": {
                "standalone_question": {"type": "string"},
                "translations": {"type": "array"},
                "keywords": {"type": "array"},
            },
        }
        result = self._models.chat(
            ChatRequest(
                self._transform_model_id,
                (
                    ChatMessage(
                        "system",
                        "Return only the requested structured query transformation. "
                        "Do not add authorization or knowledge-base constraints.",
                    ),
                    ChatMessage(
                        "user",
                        f"history={list(history)}\nquery={canonical}\n"
                        f"target_languages={list(languages)}",
                    ),
                ),
                structured_schema=schema,
                policy=InvocationPolicy(timeout_seconds=3, max_retries=0),
            )
        )
        if result.structured is None:
            raise ValueError("query transformer returned no structured output")
        return result.structured

    def _append(
        self,
        variants: list[QueryVariant],
        value: str,
        kind: QueryVariantKind,
        language: str,
    ) -> None:
        normalized = normalize_query(value, max_characters=self._max_characters)
        if normalized not in {item.text for item in variants}:
            variants.append(QueryVariant(normalized, kind, language, self._transform_model_id))


def normalize_query(value: str, *, max_characters: int = 8000) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = "".join(
        character
        for character in normalized
        if character in "\t\n\r" or unicodedata.category(character) not in {"Cc", "Cf"}
    )
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if not cleaned:
        raise ValueError("query is empty after normalization")
    return cleaned[:max_characters]


def detect_language(value: str) -> str:
    cjk = sum("\u3400" <= character <= "\u9fff" for character in value)
    latin = sum(character.isascii() and character.isalpha() for character in value)
    return "zh" if cjk > latin else "en"


def extract_keywords(value: str, *, limit: int = 16) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0).casefold() for match in _KEYWORD.finditer(value)))[
        :limit
    ]
