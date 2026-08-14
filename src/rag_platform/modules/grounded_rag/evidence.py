"""Deterministic evidence sufficiency, packaging, and citation integrity policy."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import KnowledgeBaseId, TraceId
from rag_platform.modules.grounded_rag.contracts import (
    CitationAuthority,
    CitationIntegrityError,
    EvidenceDecision,
    EvidenceItem,
    EvidencePackage,
    EvidenceStatus,
    RagBoundingBox,
    RagCitation,
)
from rag_platform.modules.knowledge.contracts import SearchHit

_CITATION_MARKER = re.compile(r"(?<!\w)\[(\d+)]")


class EvidenceSufficiencyPolicy:
    def __init__(self, *, minimum_normalized_score: float = 0.0) -> None:
        if not 0 <= minimum_normalized_score <= 1:
            raise ValueError("minimum evidence score must be within [0, 1]")
        self._minimum_score = minimum_normalized_score

    def evaluate(self, items: tuple[EvidenceItem, ...]) -> EvidenceDecision:
        usable = tuple(item for item in items if item.citation.quote in item.hit.content)
        if not usable:
            return EvidenceDecision(EvidenceStatus.NO_EVIDENCE, "no authorized eligible evidence")
        if _has_conflict(usable):
            return EvidenceDecision(
                EvidenceStatus.CONFLICTING_EVIDENCE,
                "unresolved evidence conflict",
                tuple(item.index for item in usable),
            )
        strong = tuple(item for item in usable if item.normalized_score >= self._minimum_score)
        if not strong:
            return EvidenceDecision(
                EvidenceStatus.PARTIAL_EVIDENCE,
                "authorized evidence is below the sufficiency threshold",
                tuple(item.index for item in usable),
            )
        return EvidenceDecision(
            EvidenceStatus.SUFFICIENT,
            "authorized evidence satisfies the configured threshold",
            tuple(item.index for item in strong),
        )


def build_evidence_package(
    question: str,
    trace_id: TraceId,
    hits: tuple[SearchHit, ...],
    *,
    policy: EvidenceSufficiencyPolicy,
    max_context_characters: int,
) -> EvidencePackage:
    if max_context_characters < 1:
        raise ValueError("maximum context characters must be positive")
    items: list[EvidenceItem] = []
    consumed = 0
    for hit in hits:
        marker = _evidence_marker(len(items) + 1, hit)
        if items and consumed + len(marker) > max_context_characters:
            break
        citation = citation_from_hit(hit, trace_id)
        items.append(EvidenceItem(len(items) + 1, hit, citation, _normalized_score(hit)))
        consumed += len(marker)
    evidence = tuple(items)
    decision = policy.evaluate(evidence)
    context = "".join(_evidence_marker(item.index, item.hit) for item in evidence)
    return EvidencePackage(question, trace_id, evidence, decision, context)


def validate_generated_citations(
    answer: str,
    package: EvidencePackage,
    *,
    authority: CitationAuthority,
    context: AuthorizationContext,
    knowledge_base_ids: tuple[KnowledgeBaseId, ...],
) -> tuple[RagCitation, ...]:
    indices = tuple(dict.fromkeys(int(value) for value in _CITATION_MARKER.findall(answer)))
    if not indices:
        raise CitationIntegrityError("grounded answer contains no citation markers")
    by_index = {item.index: item for item in package.items}
    if any(index not in by_index for index in indices):
        raise CitationIntegrityError("grounded answer references unknown evidence")
    selected = tuple(by_index[index] for index in indices)
    authorized = authority.validate_search_hits(
        context, knowledge_base_ids, tuple(item.hit for item in selected)
    )
    if tuple(item.chunk_id for item in authorized) != tuple(item.hit.chunk_id for item in selected):
        raise CitationIntegrityError("citation authority changed before answer publication")
    for item in selected:
        citation = item.citation
        if citation.trace_id != package.trace_id or citation.quote not in item.hit.content:
            raise CitationIntegrityError("citation quote or trace binding is invalid")
        if (
            citation.tenant_id != context.tenant_id
            or citation.knowledge_base_id not in knowledge_base_ids
        ):
            raise CitationIntegrityError("citation is outside the authorized scope")
    return tuple(item.citation for item in selected)


def citation_from_hit(hit: SearchHit, trace_id: TraceId) -> RagCitation:
    source = dict(hit.source)
    page_number = _positive_int(source.get("page_start"))
    bounding_box = _bounding_box(source.get("bounding_box")) if page_number else None
    return RagCitation(
        hit.tenant_id,
        hit.knowledge_base_id,
        hit.document_id,
        hit.document_version_id,
        hit.chunk_id,
        hit.content,
        source,
        trace_id,
        page_number,
        bounding_box,
        source.get("source_uri") or source.get("file_name"),
        source.get("media_type"),
    )


def _evidence_marker(index: int, hit: SearchHit) -> str:
    metadata = json.dumps(
        {
            "chunk_id": str(hit.chunk_id),
            "document_version_id": str(hit.document_version_id),
            "source": dict(hit.source),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f'<evidence index="{index}" metadata={json.dumps(metadata)}>{hit.content}</evidence>\n'


def _normalized_score(hit: SearchHit) -> float:
    score = hit.rerank_score if hit.rerank_score is not None else hit.score
    if not math.isfinite(score):
        return 0.0
    return max(0.0, min(1.0, score))


def _has_conflict(items: tuple[EvidenceItem, ...]) -> bool:
    claims: dict[str, set[str]] = defaultdict(set)
    for item in items:
        key = item.hit.source.get("conflict_key")
        claim = item.hit.source.get("claim")
        if key and claim:
            claims[key].add(claim.casefold().strip())
    return any(len(values) > 1 for values in claims.values())


def _positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _bounding_box(value: str | None) -> RagBoundingBox | None:
    if value is None:
        return None
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            return None
        return RagBoundingBox(
            float(raw["x0"]),
            float(raw["y0"]),
            float(raw["x1"]),
            float(raw["y1"]),
            str(raw["coordinate_space"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
