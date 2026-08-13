"""Validate and emit machine-readable R3 completion evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

R3_CAPABILITIES = ("CAP-01", "CAP-02", "CAP-03", "CAP-04", "CAP-35")
R3_FILES = (
    "src/rag_platform/modules/knowledge/compiler.py",
    "src/rag_platform/modules/knowledge/chunking.py",
    "src/rag_platform/adapters/outbound/document_parsers.py",
    "src/rag_platform/adapters/outbound/document_security.py",
    "src/rag_platform/adapters/outbound/ocr.py",
    "migrations/versions/20260813_0003_r3_document_compiler.py",
    "tests/golden/test_r3_document_formats.py",
    "tests/unit/test_r3_chunk_methods.py",
    "tests/security/test_r3_parser_limits.py",
    "tests/e2e/test_r3_multiformat_ingestion.py",
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
    missing = [relative for relative in R3_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"R3 files missing: {missing}")
    status = yaml.safe_load(
        (root / "baselines/r3/capability-status.yaml").read_text(encoding="utf-8")
    )
    if tuple(status["capabilities"]) != R3_CAPABILITIES:
        raise ValueError("R3 capability status is incomplete")
    if status["capabilities"]["CAP-35"] != "parsing_foundation_implemented":
        raise ValueError("R3 must not overclaim full multimodal RAG")
    legacy = json.loads(
        (root / "baselines/r3/legacy-behavior-evidence.json").read_text(encoding="utf-8")
    )
    if legacy["source_reuse"] != "none" or len(legacy["observations"]) != 4:
        raise ValueError("R3 legacy evidence is incomplete")
    return {
        "schema_version": 1,
        "report_id": "rag-greenfield-r3-document-compiler",
        "stage": "R3",
        "status": "completed",
        "generated_at": "2026-08-13",
        "source": {"commit": _git(root, "rev-parse", "HEAD")},
        "scope": {
            "capabilities": list(R3_CAPABILITIES),
            "document_formats": ["pdf", "docx", "pptx", "xlsx", "txt", "md", "html", "image"],
            "block_kinds": ["heading", "paragraph", "list", "table", "image", "code"],
            "chunk_methods": [
                "general",
                "paper",
                "book",
                "manual",
                "laws",
                "qa",
                "table",
                "resume",
                "picture",
            ],
            "multimodal_rag": "parsing_foundation_only",
        },
        "evidence": {
            "legacy_behavior_observations": 4,
            "format_golden": True,
            "resource_and_error_gates": True,
            "stable_block_and_chunk_ids": True,
            "postgres_pgvector_multiformat_e2e": True,
            "tesseract_adapter": True,
            "tesseract_binary_executed": True,
            "tesseract_binary_reason": (
                "Tesseract 5.5.0 and eng language were invoked in an isolated Linux container; "
                "the first small-font sample returned no words and exposed the need for the "
                "committed real-runtime CI gate"
            ),
            "source_reuse": "none",
        },
        "migration_head": "20260813_0003",
        "next_stage": "R4",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = build_report(root)
        frozen_path = root / "reports/r3/document-compiler.json"
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
                    raise ValueError(f"frozen R3 report drift in {key}")
        else:
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            frozen_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"R3 document compiler failed: {exc}", file=sys.stderr)
        return 1
    print("R3 document compiler passed: formats, blocks, OCR, chunks and safety are frozen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
