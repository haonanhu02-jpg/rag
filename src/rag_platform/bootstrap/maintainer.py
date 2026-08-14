"""Maintenance command composition root."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from rag_platform.bootstrap.common import check_process
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import Settings


def run(runtime: R2Runtime | None = None, *, dry_run: bool = True) -> int:
    if runtime is None:
        Settings.from_environment()
        return 0
    runtime.lifecycle_reconciler.run(dry_run=dry_run)
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args(arguments)
    if options.check:
        return check_process("maintainer")
    runtime = R2Runtime(Settings.from_environment())
    try:
        return run(runtime, dry_run=not options.apply)
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
