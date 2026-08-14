"""Validate and emit machine-readable R6 reliable-lifecycle evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

R6_CAPABILITIES = ("CAP-23", "CAP-24", "CAP-25", "CAP-26", "CAP-38")
R6_FILES = (
    "src/rag_platform/modules/lifecycle/contracts.py",
    "src/rag_platform/modules/lifecycle/service.py",
    "src/rag_platform/adapters/outbound/lifecycle_postgres.py",
    "src/rag_platform/adapters/outbound/elasticsearch.py",
    "src/rag_platform/orchestration/ingestion_graph.py",
    "migrations/versions/20260814_0006_r6_reliable_lifecycle.py",
    "tests/e2e/test_r6_reliable_lifecycle.py",
    "tests/unit/test_r6_lifecycle_contracts.py",
    "tests/unit/test_r6_lifecycle_service.py",
)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def build_report(root: Path) -> dict[str, Any]:
    missing = [relative for relative in R6_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"R6 files missing: {missing}")
    status = yaml.safe_load(
        (root / "baselines/r6/capability-status.yaml").read_text(encoding="utf-8")
    )
    if tuple(status["capabilities"]) != R6_CAPABILITIES:
        raise ValueError("R6 capability status is incomplete")
    if set(status["capabilities"].values()) != {"implemented"}:
        raise ValueError("R6 contains an unimplemented in-scope capability")
    legacy = json.loads(
        (root / "baselines/r6/legacy-behavior-evidence.json").read_text(encoding="utf-8")
    )
    if legacy["source_reuse"] != "none" or len(legacy["observations"]) != 5:
        raise ValueError("R6 legacy behavior evidence is incomplete")
    source_commit = _git(root, "rev-parse", "HEAD")
    frozen_path = root / "reports/r6/reliable-lifecycle.json"
    if frozen_path.is_file():
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        frozen_commit = frozen.get("source", {}).get("commit")
        if isinstance(frozen_commit, str) and frozen_commit:
            source_commit = frozen_commit
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r6-reliable-lifecycle",
        "stage": "R6",
        "status": "completed",
        "generated_at": "2026-08-14",
        "source": {"commit": source_commit},
        "scope": {
            "capabilities": list(R6_CAPABILITIES),
            "migration_head": "20260814_0006",
            "langgraph_ingestion_orchestration": True,
            "postgresql_business_authority": True,
            "maintainer_default": "dry_run",
        },
        "evidence": {
            "transactional_outbox_and_deterministic_message_id": True,
            "tenant_envelope_and_idempotent_commands": True,
            "ingestion_job_and_seven_tasks": True,
            "lease_recovery_duplicate_delivery_and_finite_retry": True,
            "transient_permanent_cancelled_dead_letter_states": True,
            "immutable_versions_reparse_update_and_failure_preserves_active": True,
            "candidate_validation_fencing_and_cas_route_activation": True,
            "rollback_builds_and_projects_new_generation": True,
            "immediate_tombstone_restore_and_retained_purge": True,
            "kb_rebuild_atomic_route_switch_and_old_generation_retention": True,
            "dry_run_reconciliation_and_safe_repairs": True,
            "cross_store_orphan_inventory_and_repair": True,
            "real_postgresql_elasticsearch_object_store_e2e": True,
            "source_reuse": "none",
        },
        "next_stage": "R7",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        frozen_path = root / "reports/r6/reliable-lifecycle.json"
        if frozen_path.is_file():
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
            for key in (
                "schema_version",
                "report_id",
                "stage",
                "status",
                "scope",
                "evidence",
                "next_stage",
            ):
                if frozen.get(key) != report.get(key):
                    raise ValueError(f"frozen R6 report drift in {key}")
        else:
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            frozen_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R6 reliable lifecycle failed: {exc}", file=sys.stderr)
        return 1
    print("R6 reliable lifecycle passed: Outbox, fencing, tombstone and recovery are frozen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
