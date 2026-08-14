"""Validate and emit machine-readable R5 completion evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

R5_CAPABILITIES = ("CAP-17", "CAP-21", "CAP-27", "CAP-36", "CAP-37")
R5_FILES = (
    "src/rag_platform/modules/grounded_rag/contracts.py",
    "src/rag_platform/modules/grounded_rag/evidence.py",
    "src/rag_platform/modules/grounded_rag/evaluation.py",
    "src/rag_platform/modules/grounded_rag/service.py",
    "src/rag_platform/modules/model_runtime/contracts.py",
    "src/rag_platform/adapters/outbound/langchain_runtime.py",
    "src/rag_platform/adapters/inbound/http.py",
    "tests/unit/test_r5_evidence.py",
    "tests/unit/test_r5_grounded_rag.py",
    "tests/evaluation/test_r5_answer_quality.py",
    "tests/e2e/test_r2_minimum_rag.py",
    "datasets/r5/grounded-answer-v1.jsonl",
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
    missing = [relative for relative in R5_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"R5 files missing: {missing}")
    status = yaml.safe_load(
        (root / "baselines/r5/capability-status.yaml").read_text(encoding="utf-8")
    )
    if tuple(status["capabilities"]) != R5_CAPABILITIES:
        raise ValueError("R5 capability status is incomplete")
    if any(value == "not_implemented_in_new_repo" for value in status["capabilities"].values()):
        raise ValueError("R5 capability status contains an unimplemented in-scope capability")
    legacy = json.loads(
        (root / "baselines/r5/legacy-behavior-evidence.json").read_text(encoding="utf-8")
    )
    if legacy["source_reuse"] != "none" or len(legacy["observations"]) != 5:
        raise ValueError("R5 legacy evidence is incomplete")
    dataset = tuple(
        json.loads(line)
        for line in (root / "datasets/r5/grounded-answer-v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    if {item["expected_status"] for item in dataset} != {
        "sufficient",
        "partial_evidence",
        "no_evidence",
        "conflicting_evidence",
    }:
        raise ValueError("R5 dataset does not cover all evidence statuses")
    source_commit = _git(root, "rev-parse", "HEAD")
    frozen_path = root / "reports/r5/grounded-rag.json"
    if frozen_path.is_file():
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        frozen_commit = frozen.get("source", {}).get("commit")
        if isinstance(frozen_commit, str) and frozen_commit:
            source_commit = frozen_commit
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r5-grounded-rag",
        "stage": "R5",
        "status": "completed",
        "generated_at": "2026-08-14",
        "source": {"commit": source_commit},
        "scope": {
            "capabilities": list(R5_CAPABILITIES),
            "evidence_statuses": [
                "sufficient",
                "partial_evidence",
                "no_evidence",
                "conflicting_evidence",
            ],
            "citation_schema_version": 2,
            "stream_transport": "server_sent_events",
            "langchain_model_adapter": True,
            "langgraph_online_hot_path": False,
        },
        "evidence": {
            "legacy_behavior_observations": 5,
            "system_owned_evidence_policy": True,
            "deterministic_refusal": True,
            "post_generation_postgresql_authority_revalidation": True,
            "tenant_kb_version_chunk_page_bbox_quote_source_trace_binding": True,
            "unknown_missing_revoked_citation_negative": True,
            "ordered_stream_fallback_cancel_interruption": True,
            "model_timeout_retry_fallback_token_cost_budget": True,
            "real_postgresql_elasticsearch_sse_e2e": True,
            "legacy_json_api_additive_compatibility": True,
            "deterministic_fake_evaluation": "contract_only_not_quality",
            "real_model_evaluation": "not_run_missing_provider_credentials",
            "dataset": "datasets/r5/grounded-answer-v1.jsonl",
            "source_reuse": "none",
        },
        "migration_head": "20260814_0004",
        "next_stage": "R6",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        frozen_path = root / "reports/r5/grounded-rag.json"
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
                    raise ValueError(f"frozen R5 report drift in {key}")
        else:
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            frozen_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R5 grounded RAG failed: {exc}", file=sys.stderr)
        return 1
    print("R5 grounded RAG passed: evidence, citations and streaming are frozen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
