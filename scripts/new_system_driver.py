"""CLI entrypoint for the new system driver; truthful R0 implementation."""

from __future__ import annotations

import json
import sys

from rag_platform.compatibility.contracts import DriverRequest
from rag_platform.compatibility.drivers import NotImplementedNewDriver


def main() -> int:
    raw = json.load(sys.stdin)
    request = DriverRequest(
        capability_id=raw["capability_id"],
        scenario_id=raw["scenario_id"],
        payload=raw.get("payload", {}),
    )
    result = NotImplementedNewDriver().invoke(request)
    print(json.dumps(result.as_json(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

