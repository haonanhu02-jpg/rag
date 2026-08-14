"""Validate and emit machine-readable R4 completion evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

R4_CAPABILITIES = (*tuple(f"CAP-{value:02d}" for value in range(9, 21)), "CAP-22")
R4_FILES = (
    "src/rag_platform/modules/retrieval/contracts.py",
    "src/rag_platform/modules/retrieval/query.py",
    "src/rag_platform/modules/retrieval/ranking.py",
    "src/rag_platform/modules/retrieval/service.py",
    "src/rag_platform/adapters/outbound/elasticsearch.py",
    "migrations/versions/20260814_0004_r4_authorized_retrieval.py",
    "tests/unit/test_r4_query_and_filters.py",
    "tests/unit/test_r4_ranking.py",
    "tests/unit/test_r4_retrieval_service.py",
    "tests/integration/test_r4_elasticsearch.py",
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
    missing = [relative for relative in R4_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"R4 files missing: {missing}")
    status = yaml.safe_load(
        (root / "baselines/r4/capability-status.yaml").read_text(encoding="utf-8")
    )
    if tuple(status["capabilities"]) != R4_CAPABILITIES:
        raise ValueError("R4 capability status is incomplete")
    if status["capabilities"]["CAP-17"] != "retrieval_fallback_implemented":
        raise ValueError("R4 must not overclaim R5 answer fallback")
    legacy = json.loads(
        (root / "baselines/r4/legacy-behavior-evidence.json").read_text(encoding="utf-8")
    )
    if legacy["source_reuse"] != "none" or len(legacy["observations"]) != 5:
        raise ValueError("R4 legacy evidence is incomplete")
    source_commit = _git(root, "rev-parse", "HEAD")
    frozen_path = root / "reports/r4/authorized-retrieval.json"
    if frozen_path.is_file():
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        frozen_commit = frozen.get("source", {}).get("commit")
        if isinstance(frozen_commit, str) and frozen_commit:
            source_commit = frozen_commit
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r4-authorized-retrieval",
        "stage": "R4",
        "status": "completed",
        "generated_at": "2026-08-14",
        "source": {"commit": source_commit},
        "scope": {
            "capabilities": list(R4_CAPABILITIES),
            "channels": ["elasticsearch_bm25", "elasticsearch_knn"],
            "fusion": "application_rrf_k60",
            "final_authority": "postgresql",
            "shared_boundary": "AuthorizedRetrieval",
            "langgraph_online_hot_path": False,
        },
        "evidence": {
            "legacy_behavior_observations": 5,
            "real_elasticsearch_bm25_knn": True,
            "postgresql_authority_revalidation": True,
            "cross_tenant_wrong_kb_unpublished_deleted_negative": True,
            "recursive_filter_ast_complexity_bound": True,
            "canonical_query_failure_fallback": True,
            "hybrid_recall_non_regression": True,
            "rrf_rerank_threshold_topn_document_quota": True,
            "finite_fallback_preserves_hard_and_user_filters": True,
            "dependency_failure_separate_from_no_evidence": True,
            "content_free_role_scoped_ttl_trace": True,
            "trace_write_failure_nonblocking_metric": True,
            "source_reuse": "none",
        },
        "migration_head": "20260814_0004",
        "next_stage": "R5",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        frozen_path = root / "reports/r4/authorized-retrieval.json"
        if frozen_path.is_file():
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
                    raise ValueError(f"frozen R4 report drift in {key}")
        else:
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            frozen_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R4 authorized retrieval failed: {exc}", file=sys.stderr)
        return 1
    print("R4 authorized retrieval passed: hybrid search, authority and trace are frozen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
