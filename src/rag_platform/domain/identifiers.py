"""Strong identifiers used at domain boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Identifier:
    value: UUID

    @classmethod
    def parse(cls, value: str | UUID) -> Identifier:
        return cls(value if isinstance(value, UUID) else UUID(value))

    def __str__(self) -> str:
        return str(self.value)


class TenantId(Identifier):
    pass


class ActorId(Identifier):
    pass


class KnowledgeBaseId(Identifier):
    pass


class DocumentId(Identifier):
    pass


class DocumentVersionId(Identifier):
    pass


class BlockId(Identifier):
    pass


class ChunkId(Identifier):
    pass


class IndexVersionId(Identifier):
    pass


class TraceId(Identifier):
    pass


class JobId(Identifier):
    pass


class OperationId(Identifier):
    pass


class AgentRunId(Identifier):
    pass
