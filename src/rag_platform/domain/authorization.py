"""Trusted authorization context construction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from rag_platform.domain.identifiers import ActorId, KnowledgeBaseId, TenantId

_SECURITY_CONTROLLED_FIELDS: Final = frozenset(
    {"tenant", "tenant_id", "actor", "actor_id", "role", "roles", "acl", "scopes"}
)


class UntrustedSecurityField(ValueError):
    """Raised when an external payload attempts to set trusted identity fields."""


@dataclass(frozen=True, slots=True)
class TrustedPrincipal:
    """Identity assertion emitted by an authenticated inbound adapter."""

    tenant_id: TenantId
    actor_id: ActorId
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.roles or any(not role.strip() for role in self.roles):
            raise ValueError("a trusted principal requires non-empty roles")


@dataclass(frozen=True, slots=True, init=False)
class AuthorizationContext:
    tenant_id: TenantId
    actor_id: ActorId
    roles: frozenset[str]
    scopes: frozenset[str]
    allowed_knowledge_bases: frozenset[KnowledgeBaseId] | None

    @classmethod
    def from_principal(
        cls,
        principal: TrustedPrincipal,
        *,
        allowed_knowledge_bases: frozenset[KnowledgeBaseId] | None = None,
    ) -> AuthorizationContext:
        context = object.__new__(cls)
        object.__setattr__(context, "tenant_id", principal.tenant_id)
        object.__setattr__(context, "actor_id", principal.actor_id)
        object.__setattr__(context, "roles", principal.roles)
        object.__setattr__(context, "scopes", principal.scopes)
        object.__setattr__(context, "allowed_knowledge_bases", allowed_knowledge_bases)
        return context

    @staticmethod
    def reject_security_fields(payload: Mapping[str, object]) -> Mapping[str, object]:
        rejected = sorted(_SECURITY_CONTROLLED_FIELDS.intersection(payload))
        if rejected:
            raise UntrustedSecurityField(
                f"security-controlled fields are not accepted: {', '.join(rejected)}"
            )
        return MappingProxyType(dict(payload))
