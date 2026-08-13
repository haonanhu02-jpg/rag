from __future__ import annotations

from uuid import UUID

import pytest

from rag_platform.domain.identifiers import BlockId
from rag_platform.modules.knowledge.chunking import CHUNK_METHODS, ChunkMethodRegistry
from rag_platform.modules.knowledge.contracts import (
    BlockKind,
    CompiledBlock,
    MediaReference,
    ParsedDocument,
    TableMetadata,
)


def _block(ordinal: int, kind: BlockKind, text: str) -> CompiledBlock:
    return CompiledBlock(
        BlockId(UUID(int=ordinal + 1)),
        ordinal,
        kind,
        text,
        ordinal * 20,
        ordinal * 20 + len(text),
        table=TableMetadata(3, 2, True) if kind is BlockKind.TABLE else None,
        media=MediaReference("image/png", "diagram.png") if kind is BlockKind.IMAGE else None,
    )


def _document() -> ParsedDocument:
    return ParsedDocument(
        2,
        "test-parser",
        "1",
        "text/markdown",
        "source.md",
        (
            _block(0, BlockKind.HEADING, "Alarm Recovery"),
            _block(1, BlockKind.PARAGRAPH, "第十二条 Inspect the relay."),
            _block(2, BlockKind.PARAGRAPH, "Q: Alarm?\nA: Reset controller."),
            _block(3, BlockKind.TABLE, "Question\tAnswer\nAlarm\tReset\nRelay\tInspect"),
            _block(4, BlockKind.IMAGE, "diagram"),
        ),
    )


@pytest.mark.parametrize("method", sorted(CHUNK_METHODS))
def test_all_nine_methods_are_stable_bounded_and_source_bound(method: str) -> None:
    chunker = ChunkMethodRegistry(max_tokens=32, overlap_tokens=4)
    first = chunker.chunk(_document(), method=method, identity_scope="scope", file_name="source.md")
    second = chunker.chunk(
        _document(), method=method, identity_scope="scope", file_name="source.md"
    )
    assert first == second
    assert first
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert all(chunk.source["chunk_method"] == method for chunk in first)
    assert all(chunk.source["source_block_ids"] for chunk in first)


def test_table_repeats_header_and_laws_split_articles() -> None:
    chunks = ChunkMethodRegistry(max_tokens=32, overlap_tokens=4).chunk(
        _document(), method="table", identity_scope="scope", file_name="source.md"
    )
    assert sum(chunk.text.startswith("Question\tAnswer") for chunk in chunks) == 2
    law_document = ParsedDocument(
        2,
        "parser",
        "1",
        "text/plain",
        "laws.txt",
        (
            _block(
                0,
                BlockKind.PARAGRAPH,
                "第一条 Scope\ncontent\n第二条 Responsibility\ncontent",
            ),
        ),
    )
    laws = ChunkMethodRegistry(max_tokens=32, overlap_tokens=4).chunk(
        law_document, method="laws", identity_scope="scope", file_name="laws.txt"
    )
    assert len(laws) == 2
