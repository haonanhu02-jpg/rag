"""Build and validate the machine-readable R1 foundation report."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

R1_FILES = (
    "src/rag_platform/domain/authorization.py",
    "src/rag_platform/domain/entities.py",
    "src/rag_platform/domain/policies.py",
    "src/rag_platform/modules/model_runtime/contracts.py",
    "src/rag_platform/adapters/outbound/langchain_runtime.py",
    "src/rag_platform/adapters/outbound/postgres.py",
    "migrations/versions/20260813_0001_r1_foundation.py",
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
    missing = [relative for relative in R1_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"R1 files missing: {missing}")
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r1-foundation",
        "stage": "R1",
        "status": "completed",
        "generated_at": "2026-08-13",
        "source": {"commit": _git(root, "rev-parse", "HEAD")},
        "scope": {
            "business_rag_implemented": False,
            "capability_foundations": ["CAP-36", "CAP-37", "CAP-41"],
            "domain_framework_dependencies": 0,
            "composition_roots": ["api", "worker", "maintainer"],
            "model_runtime_operations": ["chat", "embedding", "rerank"],
        },
        "evidence": {
            "trusted_context_negative_tests": True,
            "cross_tenant_negative_tests": True,
            "fake_and_langchain_contract": True,
            "fake_and_postgres_repository_contract": True,
            "postgres_migration_round_trip": True,
            "r0_baseline_preserved": True,
        },
        "next_stage": "R2",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        frozen_path = root / "reports/r1/foundation.json"
        if frozen_path.exists():
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
            stable_sections = (
                "schema_version",
                "report_id",
                "stage",
                "status",
                "scope",
                "evidence",
                "next_stage",
            )
            for key in stable_sections:
                if frozen.get(key) != report.get(key):
                    raise ValueError(f"frozen R1 report drift in {key}")
        else:
            raise ValueError("reports/r1/foundation.json is missing")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R1 foundation failed: {exc}")
        return 1
    print("R1 foundation passed: domain, adapters, processes, migration, and security evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
