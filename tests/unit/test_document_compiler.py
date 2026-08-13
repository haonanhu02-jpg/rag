from __future__ import annotations

from uuid import UUID

import pytest

from rag_platform.domain.identifiers import DocumentId, KnowledgeBaseId, TenantId
from rag_platform.modules.knowledge.compiler import PlainTextDocumentCompiler
from rag_platform.modules.knowledge.contracts import CompiledDocument, UnsupportedDocument


def test_compiler_normalizes_text_and_emits_stable_source_bound_chunks() -> None:
    compiler = PlainTextDocumentCompiler(chunk_characters=64, overlap_characters=8)

    def compile_document() -> CompiledDocument:
        return compiler.compile(
            tenant_id=TenantId(UUID(int=1)),
            knowledge_base_id=KnowledgeBaseId(UUID(int=2)),
            document_id=DocumentId(UUID(int=3)),
            media_type="text/markdown",
            content=("# Reset\r\n\r\nInspect relay and controller. " * 8).encode(),
            source_sha256="a" * 64,
            file_name="manual.md",
        )

    first = compile_document()
    second = compile_document()

    assert first == second
    assert len(first.chunks) > 1
    assert first.blocks[0].kind == "heading"
    assert first.chunks[0].source["start_line"] == "1"
    assert first.chunks[0].source["chunk_method"] == "general"


@pytest.mark.parametrize(
    ("media_type", "content"),
    [("application/pdf", b"text"), ("text/plain", b"\xff"), ("text/plain", b"  ")],
)
def test_compiler_rejects_outside_r2_profile(media_type: str, content: bytes) -> None:
    with pytest.raises(UnsupportedDocument):
        PlainTextDocumentCompiler().compile(
            tenant_id=TenantId(UUID(int=1)),
            knowledge_base_id=KnowledgeBaseId(UUID(int=2)),
            document_id=DocumentId(UUID(int=3)),
            media_type=media_type,
            content=content,
            source_sha256="a" * 64,
            file_name="source.txt",
        )


def test_compiler_rejects_invalid_sizing() -> None:
    with pytest.raises(ValueError, match="sizing"):
        PlainTextDocumentCompiler(chunk_characters=63)
