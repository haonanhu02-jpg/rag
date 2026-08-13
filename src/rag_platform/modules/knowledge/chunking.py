"""Deterministic chunk methods over the parser-neutral block contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import UUID, uuid5

from rag_platform.domain.identifiers import ChunkId
from rag_platform.modules.knowledge.contracts import (
    BlockKind,
    CompiledChunk,
    ParsedBlock,
    ParsedDocument,
)

CHUNK_METHODS = frozenset(
    {"general", "paper", "book", "manual", "laws", "qa", "table", "resume", "picture"}
)
CHUNK_METHOD_VERSION = "2"
_CHUNK_NAMESPACE = UUID("2ad07566-d128-5f79-8d29-d7891726195c")
_TOKENS = re.compile(r"[\u3400-\u9fff]|\w+|[^\w\s]", re.UNICODE)
_ARTICLE = re.compile(r"^(?:第[一二三四五六七八九十百千万零〇\d]+条|Article\s+\d+)", re.I)
_QA = re.compile(
    r"(?:^|\n)(?:Q(?:uestion)?|问(?:题)?)[\uFF1A:]\s*(.+?)\n+"
    r"(?:A(?:nswer)?|答(?:案)?)[\uFF1A:]\s*(.+?)(?=\n(?:Q|问)|$)",
    re.I | re.S,
)


@dataclass(frozen=True, slots=True)
class _Group:
    blocks: tuple[ParsedBlock, ...]
    text: str


class ChunkMethodRegistry:
    """Nine explicit strategies sharing stable identity and metadata rules."""

    def __init__(self, *, max_tokens: int = 400, overlap_tokens: int = 40) -> None:
        if max_tokens < 32 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("invalid token sizing")
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def chunk(
        self,
        document: ParsedDocument,
        *,
        method: str,
        identity_scope: str,
        file_name: str,
    ) -> tuple[CompiledChunk, ...]:
        if method not in CHUNK_METHODS:
            raise ValueError(f"unknown chunk method: {method}")
        values: list[CompiledChunk] = []
        for group in self._groups(document.blocks, method):
            overlap = self._overlap_tokens if method == "general" else 0
            for text in self._split(group.text, overlap=overlap):
                ordinal = len(values)
                source_ids = tuple(dict.fromkeys(str(block.id) for block in group.blocks))
                identity = uuid5(
                    _CHUNK_NAMESPACE,
                    "\x1f".join(
                        (
                            identity_scope,
                            method,
                            CHUNK_METHOD_VERSION,
                            str(ordinal),
                            ",".join(source_ids),
                            text,
                        )
                    ),
                )
                values.append(
                    CompiledChunk(
                        ChunkId(identity),
                        ordinal,
                        text,
                        self._source(document, group.blocks, method, file_name, source_ids),
                    )
                )
        return tuple(values)

    def _groups(self, blocks: tuple[ParsedBlock, ...], method: str) -> tuple[_Group, ...]:
        if method == "general":
            return self._general(blocks)
        if method in {"paper", "manual", "resume"}:
            return self._sections(blocks, isolate_media=method in {"paper", "manual"})
        if method == "book":
            return self._sections(blocks, isolate_media=False)
        if method == "laws":
            return self._laws(blocks)
        if method == "qa":
            return self._questions(blocks)
        if method == "table":
            return self._tables(blocks)
        return self._pictures(blocks)

    def _general(self, blocks: tuple[ParsedBlock, ...]) -> tuple[_Group, ...]:
        groups: list[_Group] = []
        current: list[ParsedBlock] = []
        count = 0
        for block in blocks:
            text = self._block_text(block)
            size = len(_TOKENS.findall(text))
            if current and count + size > self._max_tokens:
                groups.append(self._make_group(current))
                current, count = [], 0
            if text:
                current.append(block)
                count += size
        if current:
            groups.append(self._make_group(current))
        return tuple(groups)

    def _sections(
        self, blocks: tuple[ParsedBlock, ...], *, isolate_media: bool
    ) -> tuple[_Group, ...]:
        groups: list[_Group] = []
        current: list[ParsedBlock] = []
        for block in blocks:
            isolate = isolate_media and block.kind in {BlockKind.TABLE, BlockKind.IMAGE}
            if isolate or (block.kind == BlockKind.HEADING and current):
                if current:
                    groups.append(self._make_group(current))
                    current = []
                if isolate:
                    groups.append(self._make_group([block]))
                    continue
            current.append(block)
        if current:
            groups.append(self._make_group(current))
        return tuple(group for group in groups if group.text)

    def _laws(self, blocks: tuple[ParsedBlock, ...]) -> tuple[_Group, ...]:
        groups: list[_Group] = []
        for block in blocks:
            current: list[str] = []
            for line in (value.strip() for value in block.text.splitlines() if value.strip()):
                if _ARTICLE.match(line) and current:
                    groups.append(_Group((block,), "\n".join(current)))
                    current = []
                current.append(line)
            if current:
                groups.append(_Group((block,), "\n".join(current)))
        return tuple(groups)

    def _questions(self, blocks: tuple[ParsedBlock, ...]) -> tuple[_Group, ...]:
        groups: list[_Group] = []
        for block in blocks:
            if block.kind == BlockKind.TABLE:
                rows = [row.split("\t") for row in block.text.splitlines() if row.strip()]
                for row in rows[1:] if len(rows) > 1 else rows:
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        groups.append(
                            _Group(
                                (block,),
                                f"Question: {row[0].strip()}\nAnswer: {row[1].strip()}",
                            )
                        )
                continue
            matches = list(_QA.finditer(block.text))
            if matches:
                for match in matches:
                    groups.append(
                        _Group(
                            (block,),
                            f"Question: {match.group(1).strip()}\nAnswer: {match.group(2).strip()}",
                        )
                    )
            elif block.text.strip():
                groups.append(_Group((block,), block.text.strip()))
        return tuple(groups)

    def _tables(self, blocks: tuple[ParsedBlock, ...]) -> tuple[_Group, ...]:
        groups: list[_Group] = []
        for block in blocks:
            if block.kind != BlockKind.TABLE:
                if block.text.strip():
                    groups.append(_Group((block,), block.text.strip()))
                continue
            rows = [row for row in block.text.splitlines() if row.strip()]
            if len(rows) < 2:
                groups.append(_Group((block,), "\n".join(rows)))
                continue
            groups.extend(_Group((block,), f"{rows[0]}\n{row}") for row in rows[1:])
        return tuple(group for group in groups if group.text)

    def _pictures(self, blocks: tuple[ParsedBlock, ...]) -> tuple[_Group, ...]:
        pages: dict[int | None, list[ParsedBlock]] = {}
        for block in blocks:
            pages.setdefault(block.page_number, []).append(block)
        groups: list[_Group] = []
        for page_blocks in pages.values():
            if any(block.kind == BlockKind.IMAGE for block in page_blocks):
                groups.append(self._make_group(page_blocks))
            else:
                groups.extend(self._make_group([block]) for block in page_blocks)
        return tuple(group for group in groups if group.text)

    @staticmethod
    def _block_text(block: ParsedBlock) -> str:
        if block.text.strip():
            return block.text.strip()
        if block.media is not None:
            return f"Image: {block.media.embedded_path or 'embedded image'}"
        return ""

    def _make_group(self, blocks: list[ParsedBlock]) -> _Group:
        return _Group(tuple(blocks), "\n\n".join(filter(None, map(self._block_text, blocks))))

    def _split(self, text: str, *, overlap: int) -> tuple[str, ...]:
        matches = list(_TOKENS.finditer(text))
        if not matches:
            return ()
        values: list[str] = []
        start = 0
        while start < len(matches):
            end = min(start + self._max_tokens, len(matches))
            value = text[matches[start].start() : matches[end - 1].end()].strip()
            if value:
                values.append(value)
            if end == len(matches):
                break
            start = end - overlap
        return tuple(values)

    @staticmethod
    def _source(
        document: ParsedDocument,
        blocks: tuple[ParsedBlock, ...],
        method: str,
        file_name: str,
        source_ids: tuple[str, ...],
    ) -> dict[str, str]:
        pages = [block.page_number for block in blocks if block.page_number is not None]
        boxes = [block.bounding_box for block in blocks if block.bounding_box is not None]
        source: dict[str, str] = {
            "file_name": file_name,
            "media_type": document.source_media_type,
            "chunk_method": method,
            "chunk_method_version": CHUNK_METHOD_VERSION,
            "parser_name": document.parser_name,
            "parser_version": document.parser_version,
            "source_block_ids": ",".join(source_ids),
            "source_order_start": str(min(block.ordinal for block in blocks)),
            "source_order_end": str(max(block.ordinal for block in blocks)),
            "start_character": str(min(block.start_character for block in blocks)),
            "end_character": str(max(block.end_character for block in blocks)),
            "start_line": str(min(block.ordinal for block in blocks) + 1),
            "end_line": str(max(block.ordinal for block in blocks) + 1),
            "block_kinds": ",".join(dict.fromkeys(str(block.kind) for block in blocks)),
        }
        if pages:
            source.update(page_start=str(min(pages)), page_end=str(max(pages)))
        heading = next((block.heading_path for block in blocks if block.heading_path), ())
        if heading:
            source["heading_path"] = json.dumps(heading, ensure_ascii=False)
        if boxes and len(set(pages)) == 1:
            spaces = {box.coordinate_space for box in boxes}
            if len(spaces) == 1:
                source["bounding_box"] = json.dumps(
                    {
                        "x0": min(box.x0 for box in boxes),
                        "y0": min(box.y0 for box in boxes),
                        "x1": max(box.x1 for box in boxes),
                        "y1": max(box.y1 for box in boxes),
                        "coordinate_space": boxes[0].coordinate_space,
                    },
                    ensure_ascii=False,
                )
        return source
