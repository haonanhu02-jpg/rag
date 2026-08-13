from __future__ import annotations

from uuid import UUID

import pytest

from rag_platform.adapters.outbound.object_store import FileObjectStore
from rag_platform.domain.identifiers import TenantId


def test_file_object_store_is_tenant_contained_and_persistent(tmp_path: object) -> None:
    from pathlib import Path

    root = Path(str(tmp_path))
    store = FileObjectStore(root)
    tenant = TenantId(UUID(int=1))
    other = TenantId(UUID(int=2))

    store.put(tenant_id=tenant, key="knowledge/a/source.txt", value=b"evidence")

    assert store.get(tenant_id=tenant, key="knowledge/a/source.txt") == b"evidence"
    assert store.get(tenant_id=other, key="knowledge/a/source.txt") is None
    with pytest.raises(ValueError, match="escapes"):
        store.put(tenant_id=tenant, key="../escape", value=b"bad")
