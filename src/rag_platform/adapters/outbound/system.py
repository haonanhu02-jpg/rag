"""System clock and cryptographically random ID adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from rag_platform.domain.identifiers import Identifier

IdentifierT = TypeVar("IdentifierT", bound=Identifier)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator[IdentifierT: Identifier]:
    def __init__(self, identifier_type: type[IdentifierT]) -> None:
        self._identifier_type = identifier_type

    def new(self) -> IdentifierT:
        return self._identifier_type(uuid4())
