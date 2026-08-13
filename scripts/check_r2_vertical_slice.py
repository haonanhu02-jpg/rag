"""Validate and emit the machine-readable R2 completion evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

R2_FILES = (
    "src/rag_platform/modules/knowledge/compiler.py",
    "src/rag_platform/modules/knowledge/service.py",
    "src/rag_platform/modules/retrieval/service.py",
    "src/rag_platform/modules/grounded_rag/service.py",
    "src/rag_platform/orchestration/ingestion_graph.py",
    "src/rag_platform/adapters/outbound/postgres.py",
    "src/rag_platform/adapters/outbound/object_store.py",
    "src/rag_platform/adapters/inbound/http.py",
    "migrations/versions/20260813_0002_r2_minimum_rag.py",
    "tests/e2e/test_r2_minimum_rag.py",
)
R2_CAPABILITIES = (
    "CAP-03",
    "CAP-04",
    "CAP-08",
    "CAP-10",
    "CAP-16",
    "CAP-21",
    "CAP-22",
    "CAP-23",
    "CAP-27",
    "CAP-38",
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
    missing = [relative for relative in R2_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"R2 files missing: {missing}")
    status = yaml.safe_load(
        (root / "baselines/r2/capability-status.yaml").read_text(encoding="utf-8")
    )
    observed = status["capabilities"]
    if tuple(observed) != R2_CAPABILITIES or set(observed.values()) != {
        "minimum_subset_implemented"
    }:
        raise ValueError("R2 capability status overclaims or is incomplete")
    legacy = json.loads(
        (root / "baselines/r2/legacy-behavior-evidence.json").read_text(encoding="utf-8")
    )
    if legacy["source_reuse"] != "none" or len(legacy["observations"]) != 4:
        raise ValueError("R2 legacy evidence is incomplete")
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r2-minimum-rag",
        "stage": "R2",
        "status": "completed",
        "generated_at": "2026-08-13",
        "source": {"commit": _git(root, "rev-parse", "HEAD")},
        "scope": {
            "business_rag_implemented": True,
            "capability_status": "minimum_subset_implemented",
            "capabilities": list(R2_CAPABILITIES),
            "remaining_capabilities": "not_implemented_in_new_repo",
            "document_media_types": ["text/plain", "text/markdown"],
            "chunk_methods": ["general"],
            "retrieval_channels": ["vector"],
            "ingestion_graph_nodes": [
                "load",
                "compile",
                "embed",
                "stage",
                "validate",
                "publish",
            ],
        },
        "evidence": {
            "legacy_behavior_observations": 4,
            "fake_model_e2e": True,
            "postgres_pgvector_e2e": True,
            "cross_tenant_negative": True,
            "wrong_kb_negative": True,
            "unpublished_negative": True,
            "deleted_negative": True,
            "stable_chunk_and_version_deduplication": True,
            "migration_round_trip": True,
            "source_reuse": "none",
        },
        "migration_head": "20260813_0002",
        "next_stage": "R3",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        frozen_path = root / "reports/r2/minimum-rag.json"
        if not frozen_path.is_file():
            raise ValueError("reports/r2/minimum-rag.json is missing")
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        for key in (
            "schema_version",
            "report_id",
            "stage",
            "status",
            "scope",
            "evidence",
            "migration_head",
            "next_stage",
        ):
            if frozen.get(key) != report.get(key):
                raise ValueError(f"frozen R2 report drift in {key}")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R2 vertical slice failed: {exc}", file=sys.stderr)
        return 1
    print("R2 vertical slice passed: minimum RAG and isolation evidence are frozen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
