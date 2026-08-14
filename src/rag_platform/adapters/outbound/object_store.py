"""Tenant-contained filesystem object storage for standalone deployments."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from uuid import UUID

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

    def delete(self, *, tenant_id: TenantId, key: str) -> None:
        target = self._path(tenant_id, key)
        if target.is_file():
            target.unlink()

    def list_objects(self) -> tuple[tuple[TenantId, str], ...]:
        values: list[tuple[TenantId, str]] = []
        for tenant_root in self._root.iterdir():
            if not tenant_root.is_dir():
                continue
            try:
                tenant_id = TenantId(UUID(tenant_root.name))
            except ValueError:
                continue
            values.extend(
                (tenant_id, target.relative_to(tenant_root).as_posix())
                for target in tenant_root.rglob("*")
                if target.is_file()
            )
        return tuple(sorted(values, key=lambda item: (str(item[0]), item[1])))
