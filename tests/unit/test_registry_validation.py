from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_platform.compatibility.registry import (
    RegistryError,
    load_capability_registry,
    load_scenarios,
    validate_registry_links,
)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]\n", "must be an object"),
        ("{}\n", "requires contract_profiles and capabilities"),
        (
            "contract_profiles: {bad: []}\ncapabilities: []\n",
            "invalid contract profile",
        ),
        (
            "contract_profiles: {bad: {inputs: []}}\ncapabilities: []\n",
            "missing fields",
        ),
        (
            "contract_profiles: {}\ncapabilities: [bad]\n",
            "entry must be an object",
        ),
        (
            "contract_profiles: {}\ncapabilities: [{id: 1}]\n",
            "id must be a string",
        ),
    ],
)
def test_capability_registry_rejects_invalid_shape(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "capabilities.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RegistryError, match=message):
        load_capability_registry(path)


@pytest.mark.parametrize(
    "content",
    [
        "[]\n",
        "{}\n",
        '{"scenario_id":"S","capability_id":"CAP-01","comparator":"bad"}\n',
        '{"scenario_id":"S","capability_id":"CAP-01","comparator":"exact","payload":[]}\n',
    ],
)
def test_scenario_registry_rejects_invalid_line(tmp_path: Path, content: str) -> None:
    path = tmp_path / "scenarios.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RegistryError, match="invalid scenario on line 1"):
        load_scenarios(path)


def test_scenario_registry_skips_blank_lines_and_rejects_duplicates(tmp_path: Path) -> None:
    scenario = {
        "scenario_id": "S",
        "capability_id": "CAP-01",
        "comparator": "exact",
        "payload": {},
    }
    path = tmp_path / "scenarios.jsonl"
    path.write_text("\n" + json.dumps(scenario) + "\n" + json.dumps(scenario) + "\n")

    with pytest.raises(RegistryError, match="scenario ids must be unique"):
        load_scenarios(path)


def test_registry_link_mismatch_is_rejected(tmp_path: Path) -> None:
    capabilities = Path("baselines/r0/capabilities.yaml")
    scenarios = tmp_path / "scenarios.jsonl"
    scenarios.write_text(
        Path("baselines/r0/scenarios.jsonl")
        .read_text(encoding="utf-8")
        .replace("CAP-43-BASELINE", "CAP-43-OTHER"),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="scenario link mismatch"):
        validate_registry_links(capabilities, scenarios)
