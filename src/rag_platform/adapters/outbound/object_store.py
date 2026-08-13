"""Tenant-contained filesystem object storage for standalone deployments."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from rag_platform.domain.identifiers import TenantId


class FileObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, tenant_id: TenantId, key: str) -> Path:
        relative = PurePosixPath(key)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("object key escapes its tenant root")
        tenant_root = (self._root / str(tenant_id)).resolve()
        target = tenant_root.joinpath(*relative.parts).resolve()
        if target != tenant_root and tenant_root not in target.parents:
            raise ValueError("object key escapes its tenant root")
        return target

    def put(self, *, tenant_id: TenantId, key: str, value: bytes) -> None:
        target = self._path(tenant_id, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(value)
        temporary.replace(target)

    def get(self, *, tenant_id: TenantId, key: str) -> bytes | None:
        target = self._path(tenant_id, key)
        return target.read_bytes() if target.is_file() else None
