from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest

from rag_platform.domain.entities import (
    AgentRun,
    Block,
    Chunk,
    Citation,
    Document,
    DocumentVersion,
    IndexVersion,
    Job,
    KnowledgeBase,
    Operation,
    RetrievalCandidate,
    Tenant,
    Trace,
)
from rag_platform.domain.identifiers import (
    AgentRunId,
    BlockId,
    ChunkId,
    DocumentId,
    DocumentVersionId,
    IndexVersionId,
    JobId,
    KnowledgeBaseId,
    OperationId,
    TenantId,
    TraceId,
)


def identifier(identifier_type: type[object], suffix: int) -> object:
    return identifier_type(UUID(int=suffix))  # type: ignore[call-arg]


NOW = datetime.now(UTC)
TENANT = identifier(TenantId, 1)
KB = identifier(KnowledgeBaseId, 2)
DOCUMENT = identifier(DocumentId, 3)
VERSION = identifier(DocumentVersionId, 4)
CHUNK = identifier(ChunkId, 5)


def test_all_domain_entities_construct_with_valid_values() -> None:
    tenant = Tenant(TENANT, "tenant", NOW)  # type: ignore[arg-type]
    kb = KnowledgeBase(KB, TENANT, "kb", NOW)  # type: ignore[arg-type]
    document = Document(DOCUMENT, TENANT, KB, "external", NOW)  # type: ignore[arg-type]
    version = DocumentVersion(VERSION, TENANT, DOCUMENT, 1, "a" * 64, NOW)  # type: ignore[arg-type]
    block = Block(identifier(BlockId, 6), TENANT, VERSION, 0, "paragraph", "text")  # type: ignore[arg-type]
    chunk = Chunk(CHUNK, TENANT, VERSION, 0, "text", {"page": "1"})  # type: ignore[arg-type]
    index = IndexVersion(identifier(IndexVersionId, 7), TENANT, KB, 1, NOW)  # type: ignore[arg-type]
    candidate = RetrievalCandidate(CHUNK, TENANT, KB, VERSION, 0.5, 1, "vector")  # type: ignore[arg-type]
    citation = Citation(CHUNK, VERSION, "quote", 1)  # type: ignore[arg-type]
    trace = Trace(identifier(TraceId, 8), TENANT, NOW)  # type: ignore[arg-type]
    job = Job(identifier(JobId, 9), TENANT, "ingest", NOW)  # type: ignore[arg-type]
    operation = Operation(identifier(OperationId, 10), TENANT, "publish", NOW)  # type: ignore[arg-type]
    run = AgentRun(identifier(AgentRunId, 11), TENANT, NOW)  # type: ignore[arg-type]

    assert all(
        value is not None
        for value in (
            tenant,
            kb,
            document,
            version,
            block,
            chunk,
            index,
            candidate,
            citation,
            trace,
            job,
            operation,
            run,
        )
    )
    with pytest.raises(TypeError):
        chunk.source["page"] = "2"  # type: ignore[index]


@pytest.mark.parametrize(
    "factory,match",
    [
        (lambda: Tenant(TENANT, "", NOW), "tenant name"),  # type: ignore[arg-type]
        (lambda: KnowledgeBase(KB, TENANT, "", NOW), "knowledge base"),  # type: ignore[arg-type]
        (lambda: Document(DOCUMENT, TENANT, KB, "", NOW), "external key"),  # type: ignore[arg-type]
        (lambda: DocumentVersion(VERSION, TENANT, DOCUMENT, 1, "bad", NOW), "source_sha256"),  # type: ignore[arg-type]
        (lambda: Block(identifier(BlockId, 6), TENANT, VERSION, -1, "p", ""), "ordinal"),  # type: ignore[arg-type]
        (lambda: Block(identifier(BlockId, 6), TENANT, VERSION, 0, "", ""), "block kind"),  # type: ignore[arg-type]
        (lambda: Chunk(CHUNK, TENANT, VERSION, -1, "x"), "ordinal"),  # type: ignore[arg-type]
        (lambda: Chunk(CHUNK, TENANT, VERSION, 0, ""), "chunk text"),  # type: ignore[arg-type]
        (lambda: IndexVersion(identifier(IndexVersionId, 7), TENANT, KB, 0, NOW), "generation"),  # type: ignore[arg-type]
        (lambda: RetrievalCandidate(CHUNK, TENANT, KB, VERSION, 0, 0, "x"), "rank"),  # type: ignore[arg-type]
        (lambda: RetrievalCandidate(CHUNK, TENANT, KB, VERSION, 0, 1, ""), "channel"),  # type: ignore[arg-type]
        (lambda: Citation(CHUNK, VERSION, "", 1), "citation quote"),  # type: ignore[arg-type]
        (lambda: Citation(CHUNK, VERSION, "q", 0), "citation page"),  # type: ignore[arg-type]
        (lambda: Job(identifier(JobId, 9), TENANT, "", NOW), "job kind"),  # type: ignore[arg-type]
        (lambda: Operation(identifier(OperationId, 10), TENANT, "", NOW), "operation kind"),  # type: ignore[arg-type]
        (lambda: Trace(identifier(TraceId, 8), TENANT, datetime.now()), "timestamps"),  # type: ignore[arg-type]
    ],
)
def test_domain_invariants_fail_closed(factory: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        factory()  # type: ignore[operator]


def test_identifiers_parse_render_and_are_immutable() -> None:
    parsed = TenantId.parse("00000000-0000-0000-0000-000000000001")
    assert str(parsed) == "00000000-0000-0000-0000-000000000001"
    with pytest.raises(FrozenInstanceError):
        parsed.value = UUID(int=2)  # type: ignore[misc]
