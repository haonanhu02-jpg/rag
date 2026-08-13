"""Background worker composition root."""

from __future__ import annotations

from collections.abc import Sequence

from rag_platform.bootstrap.common import check_process, parse_check
from rag_platform.bootstrap.settings import Settings


def run() -> int:
    Settings.from_environment()
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    if parse_check(arguments):
        return check_process("worker")
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
