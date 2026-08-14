from __future__ import annotations

import json
from pathlib import Path

from scripts.check_r5_grounded_rag import build_report


def test_frozen_r5_report_matches_current_scope() -> None:
    root = Path.cwd()
    current = build_report(root)
    frozen = json.loads((root / "reports/r5/grounded-rag.json").read_text(encoding="utf-8"))
    assert frozen["scope"] == current["scope"]
    assert frozen["evidence"] == current["evidence"]
    assert frozen["migration_head"] == "20260814_0004"
    assert frozen["next_stage"] == "R6"
