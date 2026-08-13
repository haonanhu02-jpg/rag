"""Validate the frozen R0 compatibility baseline and emit a machine report."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

from rag_platform.compatibility.registry import (
    EXPECTED_CAPABILITY_IDS,
    load_capability_registry,
    load_scenarios,
    validate_registry_links,
)

TEXT_HASH_SEMANTICS = "utf8_lf_v1"
BASELINE_FILES = (
    "baselines/r0/reference-lock.json",
    "baselines/r0/evidence-manifest.json",
    "baselines/r0/capabilities.yaml",
    "baselines/r0/openapi.json",
    "baselines/r0/scenarios.jsonl",
    "docs/architecture.md",
    "docs/compatibility-and-reuse.md",
    "docs/implementation-roadmap.md",
    "docs/phases/r0-compatibility-baseline.md",
    "docs/reuse-register.yaml",
    "pyproject.toml",
)
IMMUTABLE_R0_FILES = tuple(
    path for path in BASELINE_FILES if path.startswith("baselines/r0/")
)


class BaselineError(RuntimeError):
    """Raised when an R0 baseline invariant is violated."""


def normalized_text_hash(path: Path) -> str:
    normalized = (
        path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def build_report(root: Path) -> dict[str, Any]:
    capabilities_path = root / "baselines/r0/capabilities.yaml"
    scenarios_path = root / "baselines/r0/scenarios.jsonl"
    lock_path = root / "baselines/r0/reference-lock.json"
    validate_registry_links(capabilities_path, scenarios_path)
    registry = load_capability_registry(capabilities_path)
    scenarios = load_scenarios(scenarios_path)
    reference_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    capabilities = cast(list[dict[str, object]], registry["capabilities"])
    capability_ids = tuple(cast(str, item["id"]) for item in capabilities)
    if capability_ids != EXPECTED_CAPABILITY_IDS:
        raise BaselineError("capability registry is incomplete")
    legacy_statuses = Counter(cast(str, item["legacy_status"]) for item in capabilities)
    new_statuses = Counter(cast(str, item["new_status"]) for item in capabilities)
    if new_statuses != {"not_implemented_in_new_repo": 43}:
        raise BaselineError("R0 must not claim new business implementations")
    if reference_lock["commit"] != "ce74c58e664275ff239bcbb8c841771bc4649557":
        raise BaselineError("reference commit changed without a new baseline decision")
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r0-baseline",
        "stage": "R0",
        "status": "completed",
        "generated_at": "2026-08-13",
        "source": {
            "commit": _git(root, "rev-parse", "HEAD"),
            "branch": _git(root, "branch", "--show-current"),
        },
        "reference": reference_lock,
        "capabilities": {
            "count": len(capabilities),
            "first": capability_ids[0],
            "last": capability_ids[-1],
            "legacy_status_counts": dict(sorted(legacy_statuses.items())),
            "new_status_counts": dict(sorted(new_statuses.items())),
        },
        "scenarios": {
            "count": len(scenarios),
            "capability_count": len({scenario.capability_id for scenario in scenarios}),
            "comparator_counts": dict(
                sorted(Counter(scenario.comparator.value for scenario in scenarios).items())
            ),
        },
        "contracts": {
            "file_hash_semantics": TEXT_HASH_SEMANTICS,
            "file_sha256": {
                relative: normalized_text_hash(root / relative) for relative in BASELINE_FILES
            },
        },
        "greenfield": {
            "legacy_production_dependency": False,
            "legacy_runtime_fallback": False,
            "copied_legacy_tree": False,
            "reuse_entries": 0,
        },
        "next_stage": "R1",
        "limitations": [
            "No business capability is implemented in the new repository at R0.",
            (
                "Behavior probes beyond reference metadata are installed incrementally "
                "by target stage."
            ),
            "Legacy implemented status is evidence context, not inherited new-project completion.",
        ],
    }


def write_report(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def verify_frozen_report(report: dict[str, Any], frozen: dict[str, Any]) -> None:
    """Verify stable R0 evidence while allowing source commit metadata to differ."""
    stable_sections = (
        "schema_version",
        "report_id",
        "stage",
        "status",
        "reference",
        "capabilities",
        "scenarios",
        "greenfield",
        "next_stage",
        "limitations",
    )
    for section in stable_sections:
        if frozen.get(section) != report.get(section):
            raise BaselineError(f"frozen report drift in {section}")
    current_contracts = cast(dict[str, Any], report["contracts"])
    frozen_contracts = cast(dict[str, Any], frozen.get("contracts", {}))
    if current_contracts.get("file_hash_semantics") != frozen_contracts.get(
        "file_hash_semantics"
    ):
        raise BaselineError("frozen report drift in contracts")
    current_hashes = cast(dict[str, str], current_contracts.get("file_sha256", {}))
    frozen_hashes = cast(dict[str, str], frozen_contracts.get("file_sha256", {}))
    for relative in IMMUTABLE_R0_FILES:
        if current_hashes.get(relative) != frozen_hashes.get(relative):
            raise BaselineError(f"frozen report drift in contracts: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        if args.output is not None:
            output = args.output if args.output.is_absolute() else root / args.output
            write_report(output, report)
        else:
            frozen_path = root / "reports/r0/baseline.json"
            if frozen_path.exists():
                frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
                verify_frozen_report(report, frozen)
    except (BaselineError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R0 baseline failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"R0 baseline passed: {report['capabilities']['count']} capabilities, "
        f"{report['scenarios']['count']} executable scenarios."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
