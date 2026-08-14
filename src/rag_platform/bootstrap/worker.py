"""Background worker composition root."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from rag_platform.bootstrap.common import check_process
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import Settings


def run(runtime: R2Runtime | None = None) -> int:
    if runtime is None:
        Settings.from_environment()
        return 0
    runtime.lifecycle_worker.run_once()
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--once", action="store_true")
    options = parser.parse_args(arguments)
    if options.check:
        return check_process("worker")
    runtime = R2Runtime(Settings.from_environment())
    try:
        return run(runtime)
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
