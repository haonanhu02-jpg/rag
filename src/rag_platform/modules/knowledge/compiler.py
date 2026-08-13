"""Deterministic R2 TXT/Markdown compiler with stable source-bound IDs."""

from __future__ import annotations

import hashlib
import re
from uuid import UUID, uuid5

from rag_platform.domain.identifiers import (
    BlockId,
    ChunkId,
    DocumentId,
    KnowledgeBaseId,
    TenantId,
)
from rag_platform.modules.knowledge.contracts import (
    CompiledBlock,
    CompiledChunk,
    CompiledDocument,
    UnsupportedDocument,
)

COMPILER_VERSION = "plain-text-v1"
_STABLE_NAMESPACE = UUID("540de5a4-3814-5b4e-9587-c0860034e303")
_SUPPORTED_MEDIA_TYPES = frozenset({"text/plain", "text/markdown"})
_BLOCK_PATTERN = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.DOTALL)


class PlainTextDocumentCompiler:
    def __init__(self, *, chunk_characters: int = 800, overlap_characters: int = 100) -> None:
        if chunk_characters < 64 or not 0 <= overlap_characters < chunk_characters:
            raise ValueError("invalid chunk sizing")
        self._chunk_characters = chunk_characters
        self._overlap_characters = overlap_characters

    def compile(
        self,
        *,
        tenant_id: TenantId,
        knowledge_base_id: KnowledgeBaseId,
        document_id: DocumentId,
        media_type: str,
        content: bytes,
        source_sha256: str,
        file_name: str,
    ) -> CompiledDocument:
        if media_type not in _SUPPORTED_MEDIA_TYPES:
            raise UnsupportedDocument(f"unsupported R2 media type: {media_type}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsupportedDocument("R2 text documents must be valid UTF-8") from exc
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise UnsupportedDocument("document contains no text")
        normalized_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        scope = f"{tenant_id}:{knowledge_base_id}:{document_id}:{source_sha256}:{COMPILER_VERSION}"
        blocks = self._blocks(normalized, scope, media_type)
        chunks = self._chunks(normalized, scope, file_name, media_type)
        return CompiledDocument(blocks, chunks, normalized_sha256, COMPILER_VERSION)

    @staticmethod
    def _blocks(text: str, scope: str, media_type: str) -> tuple[CompiledBlock, ...]:
        values: list[CompiledBlock] = []
        for ordinal, match in enumerate(_BLOCK_PATTERN.finditer(text)):
            block_text = match.group().strip()
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            end = start + len(block_text)
            kind = (
                "heading"
                if media_type == "text/markdown" and block_text.lstrip().startswith("#")
                else "paragraph"
            )
            identity = uuid5(_STABLE_NAMESPACE, f"block:{scope}:{start}:{end}:{kind}")
            values.append(CompiledBlock(BlockId(identity), ordinal, kind, block_text, start, end))
        return tuple(values)

    def _chunks(
        self, text: str, scope: str, file_name: str, media_type: str
    ) -> tuple[CompiledChunk, ...]:
        values: list[CompiledChunk] = []
        start = 0
        ordinal = 0
        while start < len(text):
            hard_end = min(len(text), start + self._chunk_characters)
            end = self._boundary(text, start, hard_end)
            chunk_text = text[start:end].strip()
            actual_start = start + len(text[start:end]) - len(text[start:end].lstrip())
            actual_end = actual_start + len(chunk_text)
            identity = uuid5(
                _STABLE_NAMESPACE,
                f"chunk:{scope}:{actual_start}:{actual_end}:general-v1",
            )
            source = {
                "file_name": file_name,
                "media_type": media_type,
                "start_character": str(actual_start),
                "end_character": str(actual_end),
                "start_line": str(text.count("\n", 0, actual_start) + 1),
                "end_line": str(text.count("\n", 0, actual_end) + 1),
                "chunk_method": "general",
                "compiler_version": COMPILER_VERSION,
            }
            values.append(CompiledChunk(ChunkId(identity), ordinal, chunk_text, source))
            if end == len(text):
                break
            start = max(end - self._overlap_characters, start + 1)
            ordinal += 1
        return tuple(values)

    @staticmethod
    def _boundary(text: str, start: int, hard_end: int) -> int:
        if hard_end == len(text):
            return hard_end
        lower = start + max(1, (hard_end - start) // 2)
        candidates = [text.rfind(marker, lower, hard_end) for marker in ("\n\n", "\n", " ")]
        boundary = max(candidates)
        return hard_end if boundary < lower else boundary + 1
