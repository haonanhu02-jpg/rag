from __future__ import annotations

from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from tests.fakes.r3_documents import StaticOcr

from rag_platform.adapters.outbound.document_parsers import build_document_parsers
from rag_platform.adapters.outbound.document_security import validate_ooxml
from rag_platform.domain.identifiers import DocumentId, KnowledgeBaseId, TenantId
from rag_platform.modules.knowledge.compiler import DocumentCompiler, DocumentFormatRouter
from rag_platform.modules.knowledge.contracts import (
    DocumentParseError,
    DocumentResourceLimit,
    OcrResult,
    ParserLimits,
)


def test_ooxml_zip_bomb_and_path_traversal_fail_closed() -> None:
    compressed = BytesIO()
    with ZipFile(compressed, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "A" * 20_000)
    with pytest.raises(DocumentResourceLimit) as ratio:
        validate_ooxml(compressed.getvalue(), ParserLimits(max_compression_ratio=2))
    assert ratio.value.resource == "ooxml_compression_ratio"

    traversal = BytesIO()
    with ZipFile(traversal, "w") as archive:
        archive.writestr("../payload", "bad")
    with pytest.raises(DocumentParseError) as unsafe:
        validate_ooxml(traversal.getvalue(), ParserLimits())
    assert unsafe.value.code == "parser_ooxml_unsafe"


def test_mime_extension_and_content_mismatch_is_stable() -> None:
    with pytest.raises(DocumentParseError) as mismatch:
        DocumentFormatRouter.resolve(
            file_name="payload.pdf", media_type="text/plain", content=b"not a pdf"
        )
    assert mismatch.value.code == "document_type_mismatch"


def test_file_limit_is_checked_before_parser() -> None:
    compiler = DocumentCompiler(
        build_document_parsers(StaticOcr()), limits=ParserLimits(max_file_bytes=3)
    )
    with pytest.raises(DocumentResourceLimit) as limited:
        compiler.compile(
            tenant_id=TenantId(UUID(int=1)),
            knowledge_base_id=KnowledgeBaseId(UUID(int=2)),
            document_id=DocumentId(UUID(int=3)),
            media_type="text/plain",
            content=b"four",
            source_sha256="a" * 64,
            file_name="a.txt",
        )
    assert limited.value.resource == "file_bytes"


class EmptyOcr(StaticOcr):
    def recognize(
        self, image: object, *, language: str, timeout_seconds: float
    ) -> OcrResult:
        result = super().recognize(
            image, language=language, timeout_seconds=timeout_seconds
        )
        return type(result)(result.engine_name, result.engine_version, result.language, ())


def test_image_without_ocr_text_and_missing_tesseract_have_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.fakes.r3_documents import samples

    from rag_platform.adapters.outbound.ocr import TesseractOcrAdapter

    sample = next(value for value in samples() if value.name.endswith(".png"))
    compiler = DocumentCompiler(build_document_parsers(EmptyOcr()))
    with pytest.raises(DocumentParseError) as empty:
        compiler.compile(
            tenant_id=TenantId(UUID(int=1)),
            knowledge_base_id=KnowledgeBaseId(UUID(int=2)),
            document_id=DocumentId(UUID(int=3)),
            media_type=sample.media_type,
            content=sample.content,
            source_sha256="a" * 64,
            file_name=sample.name,
        )
    assert empty.value.code == "ocr_no_text"
    monkeypatch.setattr(
        "pytesseract.pytesseract.tesseract_cmd", "missing-tesseract-r3-executable"
    )
    with pytest.raises(DocumentParseError) as missing:
        TesseractOcrAdapter().available_languages()
    assert missing.value.code == "ocr_unavailable"
