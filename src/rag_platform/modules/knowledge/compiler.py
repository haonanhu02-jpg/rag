"""Deep DocumentCompiler interface over independent parser and chunk adapters."""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import PurePath
from uuid import UUID, uuid5
from zipfile import BadZipFile, ZipFile

from rag_platform.domain.identifiers import (
    BlockId,
    DocumentId,
    DocumentVersionId,
    KnowledgeBaseId,
    TenantId,
)
from rag_platform.modules.knowledge.chunking import CHUNK_METHODS, ChunkMethodRegistry
from rag_platform.modules.knowledge.contracts import (
    BinaryDocumentParser,
    CompiledBlock,
    CompiledDocument,
    DocumentParseError,
    DocumentResourceLimit,
    ParsedDocument,
    ParsedPayload,
    ParserLimits,
    ParserRequest,
    UnsupportedDocument,
)

COMPILER_VERSION = "document-compiler-v3"
_VERSION_NAMESPACE = UUID("a44d46cf-592f-536a-9ae5-90bd9d5ef26c")
_EXTENSIONS = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
}
_MEDIA_TYPES = {
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/html": "html",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "image/png": "image",
    "image/jpeg": "image",
    "image/tiff": "image",
    "image/bmp": "image",
    "image/webp": "image",
}


class DocumentFormatRouter:
    """Reconcile declared MIME, extension, and byte signatures without guessing binary input."""

    @classmethod
    def resolve(cls, *, file_name: str, media_type: str, content: bytes) -> str:
        declared = _MEDIA_TYPES.get(media_type.casefold().split(";", maxsplit=1)[0].strip())
        extension = _EXTENSIONS.get(PurePath(file_name).suffix.casefold())
        sniffed = cls._sniff(content)
        if declared in {"text", "markdown"} and extension == declared and sniffed is None:
            return declared
        if sniffed is None:
            if declared in {"pdf", "docx", "pptx", "xlsx", "image", "html"}:
                raise DocumentParseError(
                    "document_signature_invalid", "document content lacks its required signature"
                )
            candidates = {value for value in (declared, extension) if value is not None}
        else:
            candidates = {value for value in (declared, extension, sniffed) if value is not None}
        if not candidates:
            raise UnsupportedDocument("unsupported document type")
        if len(candidates) > 1:
            raise DocumentParseError(
                "document_type_mismatch", "MIME, extension, and content disagree"
            )
        return candidates.pop()

    @staticmethod
    def _sniff(content: bytes) -> str | None:
        prefix = content[:4096].lstrip()
        if prefix.startswith(b"%PDF-"):
            return "pdf"
        if prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"BM")):
            return "image"
        if prefix[:4] in {b"II*\x00", b"MM\x00*", b"RIFF"}:
            return "image"
        if prefix.startswith(b"PK\x03\x04"):
            try:
                with ZipFile(BytesIO(content)) as archive:
                    names = set(archive.namelist())
            except BadZipFile as exc:
                raise DocumentParseError("parser_ooxml_invalid", "invalid OOXML package") from exc
            if "word/document.xml" in names:
                return "docx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            if "xl/workbook.xml" in names:
                return "xlsx"
            return None
        lowered = prefix[:1024].lower()
        if re.search(br"<!doctype\s+html|<html\b|<(?:body|head|h[1-6]|p)\b", lowered):
            return "html"
        return None


class DocumentCompiler:
    """The only ingestion-facing interface for all supported document formats."""

    def __init__(
        self,
        parsers: tuple[BinaryDocumentParser, ...],
        *,
        limits: ParserLimits | None = None,
        chunker: ChunkMethodRegistry | None = None,
        ocr_language: str = "eng",
    ) -> None:
        self._limits = limits or ParserLimits()
        self._chunker = chunker or ChunkMethodRegistry()
        self._ocr_language = ocr_language
        self._parsers: dict[str, BinaryDocumentParser] = {}
        for parser in parsers:
            for format_id in parser.format_ids:
                if format_id in self._parsers:
                    raise ValueError(f"duplicate parser for {format_id}")
                self._parsers[format_id] = parser

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
        chunk_method: str = "general",
    ) -> CompiledDocument:
        if not content:
            raise UnsupportedDocument("document is empty")
        if len(content) > self._limits.max_file_bytes:
            raise DocumentResourceLimit("file_bytes", "document exceeds byte limit")
        if chunk_method not in CHUNK_METHODS:
            raise UnsupportedDocument(f"unsupported chunk method: {chunk_method}")
        format_id = DocumentFormatRouter.resolve(
            file_name=file_name, media_type=media_type, content=content
        )
        version_id = DocumentVersionId(
            uuid5(_VERSION_NAMESPACE, f"{document_id}:{source_sha256}")
        )
        request = ParserRequest(
            tenant_id,
            knowledge_base_id,
            document_id,
            version_id,
            source_sha256,
            file_name,
            media_type,
            format_id,
            self._limits,
            self._ocr_language,
        )
        payload = self._parsers[format_id].parse(content, request)
        if not payload.blocks:
            raise DocumentParseError("parser_no_content", "document produced no usable blocks")
        parsed = ParsedDocument(
            2,
            payload.parser_name,
            payload.parser_version,
            media_type,
            file_name,
            payload.blocks,
            payload.warnings,
            payload.page_count,
        )
        scope = f"{tenant_id}:{knowledge_base_id}:{document_id}:{source_sha256}"
        chunks = self._chunker.chunk(
            parsed,
            method=chunk_method,
            identity_scope=scope,
            file_name=file_name,
        )
        if not chunks:
            raise DocumentParseError("chunk_no_content", "document produced no usable chunks")
        normalized = "\n\n".join(
            block.text.strip() for block in payload.blocks if block.text.strip()
        )
        normalized_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return CompiledDocument(
            payload.blocks,
            chunks,
            normalized_sha256,
            COMPILER_VERSION,
            parsed,
        )


class PlainTextDocumentCompiler:
    """Compatibility facade retained for callers that only need R2 text compilation."""

    def __init__(self, *, chunk_characters: int = 800, overlap_characters: int = 100) -> None:
        if chunk_characters < 64 or not 0 <= overlap_characters < chunk_characters:
            raise ValueError("invalid chunk sizing")
        max_tokens = max(32, chunk_characters // 4)
        overlap = min(max_tokens - 1, overlap_characters // 4)
        self._compiler = DocumentCompiler(
            (_CompatibilityTextParser(),),
            chunker=ChunkMethodRegistry(max_tokens=max_tokens, overlap_tokens=overlap),
        )

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
        chunk_method: str = "general",
    ) -> CompiledDocument:
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsupportedDocument("R2 text documents must be valid UTF-8") from exc
        return self._compiler.compile(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            media_type=media_type,
            content=content,
            source_sha256=source_sha256,
            file_name=file_name,
            chunk_method=chunk_method,
        )


class _CompatibilityTextParser:
    """Small UTF-8 R2 facade; the R3 runtime uses the structured outbound parser."""

    format_ids = frozenset({"text", "markdown"})

    def parse(self, content: bytes, request: ParserRequest) -> ParsedPayload:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsupportedDocument("R2 text documents must be valid UTF-8") from exc
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        values: list[CompiledBlock] = []
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", normalized, re.S):
            value = match.group().strip()
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            end = start + len(value)
            kind = (
                "heading"
                if request.format_id == "markdown" and value.startswith("#")
                else "paragraph"
            )
            identity = uuid5(
                _VERSION_NAMESPACE,
                f"compatibility-block:{request.document_id}:{request.source_sha256}:{start}:{end}:{kind}",
            )
            values.append(
                CompiledBlock(
                    BlockId(identity),
                    len(values),
                    kind,
                    value,
                    start,
                    end,
                    parser_name="plain-text",
                    parser_version="1",
                )
            )
        return ParsedPayload("plain-text", "1", tuple(values))
