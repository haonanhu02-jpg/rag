"""Knowledge-base and upload use cases for the minimum vertical slice."""

from __future__ import annotations

import hashlib
from datetime import datetime

from rag_platform.domain.authorization import AuthorizationContext
from rag_platform.domain.identifiers import ActorId, KnowledgeBaseId
from rag_platform.domain.policies import CorePolicies, ResourceNotFound
from rag_platform.modules.knowledge.chunking import CHUNK_METHODS
from rag_platform.modules.knowledge.compiler import DocumentFormatRouter
from rag_platform.modules.knowledge.contracts import (
    KnowledgeBaseRecord,
    KnowledgeRepository,
    UnsupportedDocument,
    UploadSubmission,
)
from rag_platform.modules.ports import Clock, IdGenerator, ObjectStore


class KnowledgeService:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        object_store: ObjectStore,
        knowledge_base_ids: IdGenerator[KnowledgeBaseId],
        clock: Clock,
        max_upload_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._repository = repository
        self._object_store = object_store
        self._knowledge_base_ids = knowledge_base_ids
        self._clock = clock
        self._max_upload_bytes = max_upload_bytes

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    def create_knowledge_base(
        self,
        context: AuthorizationContext,
        *,
        name: str,
        description: str = "",
        visibility: str = "private",
    ) -> KnowledgeBaseRecord:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        if not name.strip() or len(name) > 200 or len(description) > 4000:
            raise ValueError("invalid knowledge base name or description")
        if visibility not in {"private", "tenant"}:
            raise ValueError("invalid knowledge base visibility")
        now = self._now()
        value = KnowledgeBaseRecord(
            self._knowledge_base_ids.new(),
            context.tenant_id,
            ActorId(context.actor_id.value),
            name.strip(),
            description,
            visibility,
            "active",
            now,
            now,
        )
        self._repository.create_knowledge_base(value)
        return value

    def upload(
        self,
        context: AuthorizationContext,
        *,
        knowledge_base_id: KnowledgeBaseId,
        file_name: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
        chunk_method: str = "general",
    ) -> UploadSubmission:
        CorePolicies.require_role(context, "owner", "admin", "editor")
        CorePolicies.require_knowledge_base(context, knowledge_base_id)
        if self._repository.get_knowledge_base(context, knowledge_base_id) is None:
            raise ResourceNotFound("knowledge base not found")
        if not file_name.strip() or not idempotency_key.strip() or not content:
            raise ValueError("file name, content, and idempotency key are required")
        if len(content) > self._max_upload_bytes:
            raise UnsupportedDocument("upload exceeds the configured byte limit")
        if chunk_method not in CHUNK_METHODS:
            raise UnsupportedDocument(f"unsupported chunk method: {chunk_method}")
        DocumentFormatRouter.resolve(file_name=file_name, media_type=media_type, content=content)
        digest = hashlib.sha256(content).hexdigest()
        object_key = f"knowledge/{knowledge_base_id}/{digest}"
        self._object_store.put(tenant_id=context.tenant_id, key=object_key, value=content)
        return self._repository.register_upload(
            context=context,
            knowledge_base_id=knowledge_base_id,
            file_name=file_name.strip(),
            media_type=media_type,
            object_key=object_key,
            source_sha256=digest,
            size_bytes=len(content),
            idempotency_key=idempotency_key.strip(),
            chunk_method=chunk_method,
            now=self._now(),
        )

    def _now(self) -> datetime:
        now = self._clock.now()
        if not isinstance(now, datetime):
            raise TypeError("clock returned an invalid timestamp")
        return now
