"""CLI entrypoint for the isolated legacy reference metadata probe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag_platform.compatibility.contracts import DriverRequest
from rag_platform.compatibility.drivers import PinnedReferenceDriver


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    raw = json.load(sys.stdin)
    request = DriverRequest(
        capability_id=raw["capability_id"],
        scenario_id=raw["scenario_id"],
        payload=raw.get("payload", {}),
    )
    result = PinnedReferenceDriver(args.reference_root, args.expected_commit).invoke(request)
    print(json.dumps(result.as_json(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

