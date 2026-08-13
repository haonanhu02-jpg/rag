"""Load and validate the machine-readable capability and scenario registries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from rag_platform.compatibility.contracts import ComparatorKind, JsonObject

EXPECTED_CAPABILITY_IDS = tuple(f"CAP-{index:02d}" for index in range(1, 44))
REQUIRED_PROFILE_FIELDS = {"inputs", "outputs", "invariants", "errors", "security", "performance"}


class RegistryError(ValueError):
    """Raised when a compatibility registry is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    capability_id: str
    comparator: ComparatorKind
    payload: JsonObject


def load_capability_registry(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RegistryError("capability registry must be an object")
    profiles = raw.get("contract_profiles")
    capabilities = raw.get("capabilities")
    if not isinstance(profiles, dict) or not isinstance(capabilities, list):
        raise RegistryError("registry requires contract_profiles and capabilities")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not isinstance(profile, dict):
            raise RegistryError("invalid contract profile")
        missing = REQUIRED_PROFILE_FIELDS - profile.keys()
        if missing:
            raise RegistryError(f"profile {name} missing fields: {sorted(missing)}")
    ids: list[str] = []
    scenario_ids: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            raise RegistryError("capability entry must be an object")
        capability_id = item.get("id")
        if not isinstance(capability_id, str):
            raise RegistryError("capability id must be a string")
        ids.append(capability_id)
        profile_name = item.get("contract_profile")
        if profile_name not in profiles:
            raise RegistryError(f"{capability_id} references unknown profile")
        acceptance = item.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            raise RegistryError(f"{capability_id} requires an acceptance scenario")
        for scenario_id in acceptance:
            if not isinstance(scenario_id, str) or scenario_id in scenario_ids:
                raise RegistryError(f"invalid or duplicate scenario id for {capability_id}")
            scenario_ids.add(scenario_id)
    if tuple(ids) != EXPECTED_CAPABILITY_IDS:
        raise RegistryError("capabilities must be exactly CAP-01 through CAP-43 in order")
    return cast(dict[str, object], raw)


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    scenarios: list[Scenario] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise TypeError("scenario must be an object")
            payload = raw.get("payload", {})
            if not isinstance(payload, dict):
                raise TypeError("payload must be an object")
            scenarios.append(
                Scenario(
                    scenario_id=str(raw["scenario_id"]),
                    capability_id=str(raw["capability_id"]),
                    comparator=ComparatorKind(raw["comparator"]),
                    payload=payload,
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryError(f"invalid scenario on line {line_number}") from exc
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise RegistryError("scenario ids must be unique")
    return tuple(scenarios)


def validate_registry_links(capabilities_path: Path, scenarios_path: Path) -> None:
    registry = load_capability_registry(capabilities_path)
    scenarios = load_scenarios(scenarios_path)
    capability_items = cast(list[dict[str, object]], registry["capabilities"])
    declared = {
        scenario_id
        for item in capability_items
        for scenario_id in cast(list[str], item["acceptance"])
    }
    observed = {scenario.scenario_id for scenario in scenarios}
    if declared != observed:
        raise RegistryError(
            f"scenario link mismatch: missing={sorted(declared - observed)}, "
            f"unexpected={sorted(observed - declared)}"
        )
    scenario_capability = {scenario.scenario_id: scenario.capability_id for scenario in scenarios}
    for item in capability_items:
        capability_id = cast(str, item["id"])
        for scenario_id in cast(list[str], item["acceptance"]):
            if scenario_capability[scenario_id] != capability_id:
                raise RegistryError(f"{scenario_id} linked to wrong capability")
