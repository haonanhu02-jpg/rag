from __future__ import annotations

from collections import Counter
from pathlib import Path

from rag_platform.compatibility.registry import (
    EXPECTED_CAPABILITY_IDS,
    load_capability_registry,
    load_scenarios,
    validate_registry_links,
)


def test_registry_covers_all_capabilities_in_order() -> None:
    registry = load_capability_registry(Path("baselines/r0/capabilities.yaml"))
    capabilities = registry["capabilities"]

    assert isinstance(capabilities, list)
    assert tuple(item["id"] for item in capabilities) == EXPECTED_CAPABILITY_IDS
    assert all(item["new_status"] == "not_implemented_in_new_repo" for item in capabilities)


def test_legacy_statuses_are_frozen_without_overclaiming() -> None:
    registry = load_capability_registry(Path("baselines/r0/capabilities.yaml"))
    capabilities = registry["capabilities"]
    assert isinstance(capabilities, list)

    assert Counter(item["legacy_status"] for item in capabilities) == {
        "implemented": 35,
        "experimental_off": 6,
        "mixed": 1,
        "deferred": 1,
    }


def test_every_capability_has_one_executable_r0_scenario() -> None:
    scenarios = load_scenarios(Path("baselines/r0/scenarios.jsonl"))

    assert len(scenarios) == 43
    assert {scenario.capability_id for scenario in scenarios} == set(EXPECTED_CAPABILITY_IDS)
    assert all(
        scenario.scenario_id == f"{scenario.capability_id}-BASELINE"
        for scenario in scenarios
    )


def test_registry_and_scenarios_are_linked_exactly() -> None:
    validate_registry_links(
        Path("baselines/r0/capabilities.yaml"),
        Path("baselines/r0/scenarios.jsonl"),
    )
