"""Resource gates executed before expensive third-party document parsing."""

from __future__ import annotations

from io import BytesIO
from zipfile import BadZipFile, ZipFile

from rag_platform.modules.knowledge.contracts import (
    DocumentParseError,
    DocumentResourceLimit,
    ParserLimits,
)


def validate_ooxml(content: bytes, limits: ParserLimits) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
    except BadZipFile as exc:
        raise DocumentParseError("parser_ooxml_invalid", "invalid OOXML package") from exc
    if len(entries) > limits.max_archive_entries:
        raise DocumentResourceLimit("ooxml_entries", "OOXML package has too many entries")
    total = 0
    for entry in entries:
        normalized = entry.filename.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/") or entry.flag_bits & 0x1:
            raise DocumentParseError("parser_ooxml_unsafe", "unsafe OOXML archive member")
        total += entry.file_size
        if entry.file_size / max(entry.compress_size, 1) > limits.max_compression_ratio:
            raise DocumentResourceLimit(
                "ooxml_compression_ratio", "OOXML compression ratio exceeds limit"
            )
    if total > limits.max_uncompressed_bytes:
        raise DocumentResourceLimit(
            "ooxml_uncompressed_bytes", "OOXML package expands beyond limit"
        )


def validate_image_size(width: int, height: int, limits: ParserLimits) -> None:
    if width < 1 or height < 1 or width * height > limits.max_image_pixels:
        raise DocumentResourceLimit("image_pixels", "image exceeds pixel limit")
