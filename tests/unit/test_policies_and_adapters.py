from __future__ import annotations

from uuid import UUID

import pytest

from rag_platform.adapters.outbound.memory import (
    HealthySearchIndex,
    InMemoryKnowledgeBaseRepository,
    InMemoryMessageQueue,
    InMemoryObjectStore,
    InMemoryTransactionManager,
)
from rag_platform.adapters.outbound.system import SystemClock, UuidGenerator
from rag_platform.domain import ActorId, AuthorizationContext, KnowledgeBaseId, TenantId
from rag_platform.domain.authorization import TrustedPrincipal
from rag_platform.domain.policies import (
    AccessDenied,
    BudgetExceeded,
    CorePolicies,
    ErrorClass,
    ResourceBudget,
    ResourceNotFound,
)

TENANT = TenantId(UUID(int=1))
KB = KnowledgeBaseId(UUID(int=2))
CONTEXT = AuthorizationContext.from_principal(
    TrustedPrincipal(TENANT, ActorId(UUID(int=3)), frozenset({"reader"})),
    allowed_knowledge_bases=frozenset({KB}),
)


def test_policy_success_paths_and_error_classification() -> None:
    CorePolicies.require_tenant(CONTEXT, TENANT)
    CorePolicies.require_knowledge_base(CONTEXT, KB)
    CorePolicies.require_role(CONTEXT, "reader", "admin")
    assert CorePolicies.scope(CONTEXT).tenant_id == TENANT
    with pytest.raises(AccessDenied):
        CorePolicies.require_role(CONTEXT, "admin")
    errors = (
        (AccessDenied(), ErrorClass.AUTHORIZATION),
        (ResourceNotFound(), ErrorClass.NOT_FOUND),
        (BudgetExceeded(), ErrorClass.BUDGET),
        (ValueError(), ErrorClass.VALIDATION),
        (TimeoutError(), ErrorClass.DEPENDENCY),
        (RuntimeError(), ErrorClass.INTERNAL),
    )
    assert all(CorePolicies.classify_error(error) is expected for error, expected in errors)
    with pytest.raises(ValueError):
        ResourceBudget(0, 0, 1)


def test_in_memory_adapter_set_and_transaction_paths() -> None:
    transaction_manager = InMemoryTransactionManager()
    committed = transaction_manager.transaction()
    with committed:
        pass
    assert committed.committed and not committed.rolled_back

    rolled_back = transaction_manager.transaction()
    with pytest.raises(RuntimeError), rolled_back:
        raise RuntimeError("boom")
    assert rolled_back.rolled_back and not rolled_back.committed

    objects = InMemoryObjectStore()
    objects.put(tenant_id=TENANT, key="a", value=b"value")
    assert objects.get(tenant_id=TENANT, key="a") == b"value"
    assert objects.get(tenant_id=TenantId(UUID(int=9)), key="a") is None

    queue = InMemoryMessageQueue()
    queue.publish("jobs", "1")
    assert queue.messages == [("jobs", "1")]
    assert HealthySearchIndex().healthcheck()
    assert SystemClock().now().tzinfo is not None
    assert UuidGenerator(KnowledgeBaseId).new() != KB


def test_in_memory_repository_rejects_duplicate() -> None:
    from datetime import UTC, datetime

    from rag_platform.domain import KnowledgeBase

    repository = InMemoryKnowledgeBaseRepository()
    item = KnowledgeBase(KB, TENANT, "kb", datetime.now(UTC))
    repository.add(item)
    with pytest.raises(ValueError, match="already exists"):
        repository.add(item)
