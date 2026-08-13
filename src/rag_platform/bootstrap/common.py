"""Shared check-mode behavior for process composition roots."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from rag_platform.bootstrap.settings import ConfigurationError, Settings


def check_process(process: str) -> int:
    try:
        settings = Settings.from_environment()
    except ConfigurationError as exc:
        print(json.dumps({"process": process, "status": "invalid", "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "process": process,
                "status": "ready",
                "environment": settings.environment,
                "database": "configured",
            }
        )
    )
    return 0


def parse_check(arguments: Sequence[str] | None) -> bool:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--check", action="store_true")
    return bool(parser.parse_args(arguments).check)
