from __future__ import annotations

import json
from pathlib import Path

from scripts.check_r2_vertical_slice import R2_CAPABILITIES, build_report


def test_r2_report_marks_only_minimum_subsets_complete() -> None:
    report = build_report(Path.cwd())

    assert report["scope"]["business_rag_implemented"] is True
    assert tuple(report["scope"]["capabilities"]) == R2_CAPABILITIES
    assert report["scope"]["capability_status"] == "minimum_subset_implemented"
    assert report["scope"]["remaining_capabilities"] == "not_implemented_in_new_repo"


def test_frozen_r2_report_matches_stable_evidence() -> None:
    root = Path.cwd()
    current = build_report(root)
    frozen = json.loads((root / "reports/r2/minimum-rag.json").read_text(encoding="utf-8"))

    assert frozen["scope"] == current["scope"]
    assert frozen["evidence"] == current["evidence"]
