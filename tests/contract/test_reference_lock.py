from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts.capture_legacy_openapi import normalized_json_hash

from rag_platform.compatibility import DriverRequest, DriverStatus, PinnedReferenceDriver
from rag_platform.compatibility.registry import load_scenarios

EXPECTED_COMMIT = "ce74c58e664275ff239bcbb8c841771bc4649557"


def _reference_root() -> Path:
    configured = os.environ.get("RAG_LEGACY_REFERENCE_ROOT")
    return Path(configured if configured is not None else "../legacy-r0-pin").resolve()


def test_reference_lock_is_immutable_and_isolated() -> None:
    lock = json.loads(Path("baselines/r0/reference-lock.json").read_text(encoding="utf-8"))

    assert lock["commit"] == EXPECTED_COMMIT
    assert lock["evidence"]["openapi_path_count"] == 22
    assert lock["evidence"]["test_file_count"] == 108
    assert lock["isolation"] == {
        "production_dependency_allowed": False,
        "runtime_fallback_allowed": False,
        "driver_mode": "detached_checkout_or_container",
        "dirty_checkout_allowed": False,
    }


def test_captured_openapi_matches_locked_public_contract() -> None:
    lock = json.loads(Path("baselines/r0/reference-lock.json").read_text(encoding="utf-8"))
    openapi = json.loads(Path("baselines/r0/openapi.json").read_text(encoding="utf-8"))

    assert len(openapi["paths"]) == lock["evidence"]["openapi_path_count"]
    assert normalized_json_hash(openapi) == lock["evidence"]["openapi_sha256"]


def test_evidence_manifest_objects_exist_in_pinned_reference() -> None:
    manifest = json.loads(
        Path("baselines/r0/evidence-manifest.json").read_text(encoding="utf-8")
    )
    root = _reference_root()

    for artifact in manifest["artifacts"]:
        observed = subprocess.run(
            ["git", "cat-file", "-t", artifact["git_object_sha"]],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        assert observed == artifact["git_object_type"], artifact["path"]


def test_pinned_reference_driver_rejects_wrong_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "test"], cwd=tmp_path, check=True, capture_output=True)

    result = PinnedReferenceDriver(tmp_path, EXPECTED_COMMIT).invoke(
        DriverRequest("CAP-01", "CAP-01-BASELINE")
    )

    assert result.status is DriverStatus.FAILED
    assert result.error is not None
    assert result.error["code"] == "reference_checkout_mismatch"


def test_pinned_reference_driver_rejects_unregistered_scenario() -> None:
    root = _reference_root()
    driver = PinnedReferenceDriver(root, EXPECTED_COMMIT)

    result = driver.invoke(DriverRequest("CAP-01", "UNKNOWN"))

    assert result.status is DriverStatus.UNAVAILABLE
    assert result.error == {"code": "legacy_scenario_not_registered"}


def test_pinned_reference_metadata_probe() -> None:
    root = _reference_root()
    driver = PinnedReferenceDriver(root, EXPECTED_COMMIT)

    result = driver.invoke(DriverRequest("R0", "R0-REFERENCE-METADATA"))

    assert result.status is DriverStatus.SUCCEEDED
    assert result.output == {
        "commit": EXPECTED_COMMIT,
        "tree": "862b341cb6ad06796eea555c9e16007256a30695",
        "dirty": False,
    }


@pytest.mark.skipif(
    not _reference_root().exists(),
    reason=(
        "isolated pinned reference checkout is only present in the local "
        "R0 verification environment"
    ),
)
def test_all_r0_scenarios_are_repeatable_against_pinned_reference() -> None:
    root = _reference_root()
    driver = PinnedReferenceDriver(root, EXPECTED_COMMIT)

    for scenario in load_scenarios(Path("baselines/r0/scenarios.jsonl")):
        request = DriverRequest(scenario.capability_id, scenario.scenario_id, scenario.payload)
        first = driver.invoke(request)
        second = driver.invoke(request)
        assert first.status is DriverStatus.SUCCEEDED, scenario.scenario_id
        assert first == second, scenario.scenario_id
