from __future__ import annotations

from pathlib import Path

from scripts.check_r6_reliable_lifecycle import R6_CAPABILITIES, build_report


def test_r6_report_freezes_complete_lifecycle_scope() -> None:
    report = build_report(Path.cwd())
    assert tuple(report["scope"]["capabilities"]) == R6_CAPABILITIES
    assert report["scope"]["migration_head"] == "20260814_0006"
    assert report["evidence"]["transactional_outbox_and_deterministic_message_id"] is True
    assert report["evidence"]["rollback_builds_and_projects_new_generation"] is True
    assert report["evidence"]["real_postgresql_elasticsearch_object_store_e2e"] is True
    assert report["next_stage"] == "R7"
