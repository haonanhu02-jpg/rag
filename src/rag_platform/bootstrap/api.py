"""HTTP API composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from rag_platform.adapters.inbound.http import build_router, install_error_handlers
from rag_platform.bootstrap.common import check_process, parse_check
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import Settings


def create_app(settings: Settings | None = None, runtime: R2Runtime | None = None) -> FastAPI:
    active_settings = settings or Settings.from_environment()
    active_runtime = runtime or R2Runtime(active_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        active_runtime.close()

    app = FastAPI(title="RAG Platform", version="0.4.0", lifespan=lifespan)
    app.include_router(build_router(active_runtime))
    install_error_handlers(app)
    app.state.r2_runtime = active_runtime

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok", "process": "api"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        active_settings.validate()
        return {"status": "ready", "database": "configured"}

    return app


def main(arguments: Sequence[str] | None = None) -> int:
    if parse_check(arguments):
        return check_process("api")
    settings = Settings.from_environment()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
