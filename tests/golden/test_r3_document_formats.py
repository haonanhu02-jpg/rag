from __future__ import annotations

import hashlib
from uuid import UUID

from tests.fakes.r3_documents import StaticOcr, blank_pdf, samples

from rag_platform.adapters.outbound.document_parsers import build_document_parsers
from rag_platform.domain.identifiers import DocumentId, KnowledgeBaseId, TenantId
from rag_platform.modules.knowledge.compiler import DocumentCompiler
from rag_platform.modules.knowledge.contracts import CompiledDocument


def _compiler(ocr: StaticOcr | None = None) -> DocumentCompiler:
    return DocumentCompiler(build_document_parsers(ocr or StaticOcr()))


def _compile(
    name: str, media_type: str, content: bytes, ocr: StaticOcr | None = None
) -> CompiledDocument:
    return _compiler(ocr).compile(
        tenant_id=TenantId(UUID(int=1)),
        knowledge_base_id=KnowledgeBaseId(UUID(int=2)),
        document_id=DocumentId(UUID(int=3)),
        media_type=media_type,
        content=content,
        source_sha256=hashlib.sha256(content).hexdigest(),
        file_name=name,
    )


def test_eight_formats_have_stable_structure_order_and_ids() -> None:
    for sample in samples():
        first = _compile(sample.name, sample.media_type, sample.content)
        second = _compile(sample.name, sample.media_type, sample.content)
        assert first == second
        assert sample.expected_kinds <= {str(block.kind) for block in first.blocks}, sample.name
        assert [block.ordinal for block in first.blocks] == list(range(len(first.blocks)))
        assert len({block.id for block in first.blocks}) == len(first.blocks)
        assert first.parsed_document is not None
        assert first.parsed_document.schema_version == 2
        assert first.chunks


def test_html_sanitizes_active_content_and_preserves_image_path() -> None:
    sample = next(value for value in samples() if value.name.endswith(".html"))
    result = _compile(sample.name, sample.media_type, sample.content)
    assert "secret" not in "\n".join(block.text for block in result.blocks)
    image = next(block for block in result.blocks if block.kind == "image")
    assert image.media is not None
    assert image.media.embedded_path == "relay.png"


def test_pdf_geometry_table_shape_and_scanned_page_fallback() -> None:
    sample = next(value for value in samples() if value.name.endswith(".pdf"))
    result = _compile(sample.name, sample.media_type, sample.content)
    assert all(block.page_number == 1 for block in result.blocks)
    assert all(block.bounding_box is not None for block in result.blocks)
    table = next(block for block in result.blocks if block.kind == "table")
    assert table.table is not None
    assert (table.table.rows, table.table.columns) == (2, 2)

    ocr = StaticOcr()
    scanned = _compile("scanned.pdf", "application/pdf", blank_pdf(), ocr)
    assert ocr.calls == [("eng", 30.0)]
    assert scanned.parsed_document is not None
    assert [warning.code for warning in scanned.parsed_document.warnings] == [
        "pdf_scanned_page_ocr"
    ]


def test_xlsx_preserves_formula_and_emits_explicit_degradation() -> None:
    sample = next(value for value in samples() if value.name.endswith(".xlsx"))
    result = _compile(sample.name, sample.media_type, sample.content)
    assert "CONCAT" in result.blocks[0].text
    assert result.parsed_document is not None
    assert {warning.code for warning in result.parsed_document.warnings} == {
        "xlsx_formula_preserved_not_evaluated",
        "xlsx_merged_cells_present",
    }


def test_image_uses_ocr_and_preserves_pixel_geometry() -> None:
    sample = next(value for value in samples() if value.name.endswith(".png"))
    ocr = StaticOcr()
    result = _compile(sample.name, sample.media_type, sample.content, ocr)
    assert ocr.calls == [("eng", 30.0)]
    assert result.blocks[0].kind == "image"
    assert result.blocks[0].media is not None
    assert result.blocks[0].media.width == 480
    assert "alarm reset inspection" in result.blocks[1].text
