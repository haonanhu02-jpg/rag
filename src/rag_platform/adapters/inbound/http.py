"""FastAPI translation layer for the current public contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import StreamingResponse

from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.domain.authorization import AuthorizationContext, TrustedPrincipal
from rag_platform.domain.identifiers import (
    ActorId,
    JobId,
    KnowledgeBaseId,
    TenantId,
    TraceId,
)
from rag_platform.domain.policies import AccessDenied, ResourceNotFound
from rag_platform.modules.grounded_rag import (
    CancellationToken,
    CitationIntegrityError,
    FixedRagAnswer,
    GenerationBudgetExceeded,
    GenerationCancelled,
    RagCitation,
    RagStreamEvent,
)
from rag_platform.modules.knowledge.contracts import IdempotencyConflict, UnsupportedDocument
from rag_platform.modules.model_runtime.contracts import ModelRuntimeError, ModelTimeout
from rag_platform.modules.retrieval.contracts import (
    FilterExpression,
    SearchDependencyError,
    combine_filters,
    parse_filter_expression,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateKnowledgeBaseBody(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    visibility: Literal["private", "tenant"] = "private"


class FixedRagBody(ApiModel):
    question: str = Field(min_length=1, max_length=8000)
    knowledge_base_ids: list[UUID] = Field(min_length=1)
    top_k: int = Field(default=20, ge=1, le=1000)
    top_n: int = Field(default=5, ge=1, le=50)
    history: list[Annotated[str, Field(min_length=1, max_length=8000)]] = Field(
        default_factory=list, max_length=16
    )
    target_languages: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=4
    )
    filters: list[dict[str, object]] = Field(default_factory=list, max_length=64)
    filter_expression: dict[str, object] | None = None

    @model_validator(mode="after")
    def top_n_is_within_top_k(self) -> FixedRagBody:
        if self.top_n > self.top_k:
            raise ValueError("top_n cannot exceed top_k")
        return self


def _user_filter(body: FixedRagBody) -> FilterExpression | None:
    expressions = tuple(parse_filter_expression(item) for item in body.filters)
    if body.filter_expression is not None:
        expressions += (parse_filter_expression(body.filter_expression),)
    return combine_filters(expressions)


def _citation_payload(item: RagCitation) -> dict[str, object]:
    bounding_box = item.bounding_box
    return {
        "schema_version": item.schema_version,
        "tenant_id": str(item.tenant_id),
        "knowledge_base_id": str(item.knowledge_base_id),
        "document_id": str(item.document_id),
        "document_version_id": str(item.document_version_id),
        "chunk_id": str(item.chunk_id),
        "quote": item.quote,
        "page_number": item.page_number,
        "bounding_box": (
            None
            if bounding_box is None
            else {
                "x0": bounding_box.x0,
                "y0": bounding_box.y0,
                "x1": bounding_box.x1,
                "y1": bounding_box.y1,
                "coordinate_space": bounding_box.coordinate_space,
            }
        ),
        "source_uri": item.source_uri,
        "media_kind": item.media_kind,
        "source": dict(item.source),
        "trace_id": str(item.trace_id),
    }


def _answer_payload(answer: FixedRagAnswer) -> dict[str, object]:
    return {
        "status": answer.status,
        "evidence_status": answer.evidence_status.value,
        "evidence_reason": answer.evidence_reason,
        "answer": answer.answer,
        "citations": [_citation_payload(item) for item in answer.citations],
        "trace_id": str(answer.trace_id),
        "prompt_version": answer.prompt_version,
        "model_id": answer.model_id,
        "input_tokens": answer.input_tokens,
        "output_tokens": answer.output_tokens,
        "cost_microunits": answer.cost_microunits,
        "model_attempts": answer.model_attempts,
        "degradation_steps": list(answer.degradation_steps),
    }


def _stream_payload(event: RagStreamEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "sequence": event.sequence,
        "event": event.event,
        "attributes": dict(event.attributes),
    }
    if event.delta:
        payload["delta"] = event.delta
    if event.answer is not None:
        payload["answer"] = _answer_payload(event.answer)
    return payload


def _context(
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_actor_id: Annotated[str | None, Header()] = None,
    x_roles: Annotated[str, Header()] = "owner",
) -> AuthorizationContext:
    if x_tenant_id is None or x_actor_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "trusted identity headers are required")
    try:
        principal = TrustedPrincipal(
            TenantId(UUID(x_tenant_id)),
            ActorId(UUID(x_actor_id)),
            frozenset(role.strip() for role in x_roles.split(",") if role.strip()),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "trusted identity is invalid") from exc
    return AuthorizationContext.from_principal(principal)


def build_router(runtime: R2Runtime) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/knowledge-bases", status_code=201, tags=["knowledge"])
    def create_knowledge_base(
        body: CreateKnowledgeBaseBody,
        context: Annotated[AuthorizationContext, Depends(_context)],
    ) -> dict[str, object]:
        value = runtime.knowledge.create_knowledge_base(
            context,
            name=body.name,
            description=body.description,
            visibility=body.visibility,
        )
        return {
            "id": str(value.id),
            "tenant_id": str(value.tenant_id),
            "owner_id": str(value.owner_id),
            "name": value.name,
            "description": value.description,
            "visibility": value.visibility,
            "status": value.status,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
        }

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/documents",
        status_code=202,
        tags=["knowledge"],
    )
    def upload_document(
        knowledge_base_id: UUID,
        file: Annotated[UploadFile, File()],
        context: Annotated[AuthorizationContext, Depends(_context)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        chunk_method: Annotated[str, Form()] = "general",
    ) -> dict[str, object]:
        content = file.file.read(runtime.knowledge.max_upload_bytes + 1)
        submitted = runtime.knowledge.upload(
            context,
            knowledge_base_id=KnowledgeBaseId(knowledge_base_id),
            file_name=file.filename or "source",
            media_type=(file.content_type or "application/octet-stream").split(";", maxsplit=1)[0],
            content=content,
            idempotency_key=idempotency_key,
            chunk_method=chunk_method,
        )
        return {
            "job_id": str(submitted.job.id),
            "document_id": str(submitted.job.document_id),
            "document_version_id": str(submitted.job.document_version_id),
            "status": submitted.job.status.value,
            "duplicate": submitted.duplicate,
        }

    @router.get("/ingestion-jobs/{job_id}", tags=["knowledge"])
    def get_ingestion_job(
        job_id: UUID,
        context: Annotated[AuthorizationContext, Depends(_context)],
    ) -> dict[str, object]:
        value = runtime.repository.get_job(context, JobId(job_id))
        if value is None:
            raise ResourceNotFound("ingestion job not found")
        return {
            "id": str(value.id),
            "tenant_id": str(value.tenant_id),
            "knowledge_base_id": str(value.knowledge_base_id),
            "document_id": str(value.document_id),
            "document_version_id": str(value.document_version_id),
            "requested_by": str(value.requested_by),
            "idempotency_key": value.idempotency_key,
            "trace_id": str(value.trace_id),
            "status": value.status.value,
            "progress": value.progress,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
            "error": (
                None
                if value.error_code is None
                else {"code": value.error_code, "message": value.error_message}
            ),
            "operation_id": None,
            "schema_version": 1,
        }

    @router.post("/rag/query", tags=["knowledge"])
    def fixed_rag(
        body: FixedRagBody,
        context: Annotated[AuthorizationContext, Depends(_context)],
        x_request_id: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        try:
            user_filter = _user_filter(body)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        answer = runtime.grounded_rag.answer(
            context,
            question=body.question,
            knowledge_base_ids=tuple(KnowledgeBaseId(value) for value in body.knowledge_base_ids),
            top_k=body.top_k,
            top_n=body.top_n,
            history=tuple(body.history),
            target_languages=tuple(body.target_languages),
            user_filter=user_filter,
            request_id=x_request_id,
        )
        return _answer_payload(answer)

    @router.post("/rag/query/stream", tags=["knowledge"])
    def stream_fixed_rag(
        body: FixedRagBody,
        context: Annotated[AuthorizationContext, Depends(_context)],
        x_request_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        try:
            user_filter = _user_filter(body)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        cancellation = CancellationToken()

        def events() -> Iterator[str]:
            try:
                for event in runtime.grounded_rag.stream_answer(
                    context,
                    question=body.question,
                    knowledge_base_ids=tuple(
                        KnowledgeBaseId(value) for value in body.knowledge_base_ids
                    ),
                    top_k=body.top_k,
                    top_n=body.top_n,
                    history=tuple(body.history),
                    target_languages=tuple(body.target_languages),
                    user_filter=user_filter,
                    request_id=x_request_id,
                    cancellation=cancellation,
                ):
                    data = json.dumps(
                        _stream_payload(event), ensure_ascii=False, separators=(",", ":")
                    )
                    yield f"id: {event.sequence}\nevent: {event.event}\ndata: {data}\n\n"
            finally:
                cancellation.cancel()

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.get("/retrieval-traces/{trace_id}", tags=["knowledge"])
    def get_trace(
        trace_id: UUID,
        context: Annotated[AuthorizationContext, Depends(_context)],
    ) -> dict[str, object]:
        value = runtime.repository.get_trace(context, TraceId(trace_id))
        if value is None:
            return {"trace_id": str(trace_id), "found": False}
        return {
            "found": True,
            "trace_id": str(value.id),
            "tenant_id": str(value.tenant_id),
            "knowledge_base_ids": [str(item) for item in value.knowledge_base_ids],
            "query_sha256": value.query_sha256,
            "status": value.status,
            "candidate_count": value.candidate_count,
            "selected_chunk_ids": [str(item) for item in value.selected_chunk_ids],
            "authorization_applied": value.authorization_applied,
            "created_at": value.created_at,
            "canonical_query_sha256": value.canonical_query_sha256,
            "query_variant_sha256": list(value.query_variant_sha256),
            "events": list(value.events),
            "candidates": list(value.candidate_traces),
            "fallback_steps": list(value.fallback_steps),
            "filter_summary": list(value.filter_summary),
            "provider_ids": list(value.provider_ids),
            "completed_at": value.completed_at,
            "expires_at": value.expires_at,
            "error_code": value.error_code,
            "request_id": value.request_id,
            "index_version_ids": [str(item) for item in value.index_version_ids],
        }

    return router


def install_error_handlers(app: object) -> None:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    active = app
    if not isinstance(active, FastAPI):
        raise TypeError("expected FastAPI")

    def handler(code: str, status_code: int) -> Callable[[Request, Exception], JSONResponse]:
        def respond(request: Request, error: Exception) -> JSONResponse:
            trace_id = request.headers.get("x-request-id", "")
            return JSONResponse(
                status_code=status_code,
                content={"error": {"code": code, "message": str(error), "trace_id": trace_id}},
            )

        return respond

    active.add_exception_handler(ResourceNotFound, handler("not_found", 404))
    active.add_exception_handler(AccessDenied, handler("forbidden", 403))
    active.add_exception_handler(IdempotencyConflict, handler("idempotency_conflict", 409))
    active.add_exception_handler(UnsupportedDocument, handler("unsupported_document", 415))
    active.add_exception_handler(SearchDependencyError, handler("search_dependency_failed", 503))
    active.add_exception_handler(ModelTimeout, handler("model_timeout", 504))
    active.add_exception_handler(ModelRuntimeError, handler("model_dependency_failed", 502))
    active.add_exception_handler(CitationIntegrityError, handler("citation_integrity_failed", 502))
    active.add_exception_handler(
        GenerationBudgetExceeded, handler("generation_budget_exceeded", 429)
    )
    active.add_exception_handler(GenerationCancelled, handler("generation_cancelled", 409))
