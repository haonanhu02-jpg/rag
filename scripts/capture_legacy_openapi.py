"""Capture the public OpenAPI contract from an isolated pinned legacy checkout."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast


class OpenApiApplication(Protocol):
    def openapi(self) -> dict[str, Any]: ...


def normalized_json_hash(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def capture() -> dict[str, Any]:
    create_app = cast(
        Callable[..., OpenApiApplication],
        importlib.import_module("ragflow_agent.api").create_app,
    )
    check_settings = cast(
        Callable[[], object],
        importlib.import_module("ragflow_agent.bootstrap.api")._check_settings,
    )
    build_runtime = cast(
        Callable[[object], object],
        importlib.import_module(
            "ragflow_agent.knowledge.runtime"
        ).build_minimum_rag_runtime,
    )
    settings = check_settings()
    app = create_app(
        settings,
        minimum_rag_runtime=build_runtime(settings),
        enable_agentic_runtime=True,
    )
    return app.openapi()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = capture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"Captured {len(snapshot['paths'])} OpenAPI paths; "
        f"normalized SHA-256 {normalized_json_hash(snapshot)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
