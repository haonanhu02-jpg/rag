"""Stable contracts for isolated old/new compatibility drivers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

type JsonScalar = bool | int | float | str | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class DriverStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_IMPLEMENTED = "not_implemented_in_new_repo"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ComparatorKind(StrEnum):
    EXACT = "exact"
    STRUCTURED = "structured"
    SET = "set"
    RANKING = "ranking"
    NUMERIC = "numeric"
    SEMANTIC = "semantic"
    STATE_SEQUENCE = "state_sequence"
    SECURITY_NEGATIVE = "security_negative"


class ComparisonClassification(StrEnum):
    EQUIVALENT = "equivalent"
    APPROVED_IMPROVEMENT = "approved_improvement"
    PRESERVED_LIMITATION = "preserved_limitation"
    REGRESSION = "regression"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True, slots=True)
class DriverRequest:
    capability_id: str
    scenario_id: str
    payload: JsonObject = field(default_factory=dict)

    def as_json(self) -> JsonObject:
        return {
            "capability_id": self.capability_id,
            "scenario_id": self.scenario_id,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class DriverResult:
    status: DriverStatus
    output: JsonValue = None
    error: JsonObject | None = None

    def as_json(self) -> JsonObject:
        value: JsonObject = {"status": self.status.value, "output": self.output}
        if self.error is not None:
            value["error"] = self.error
        return value


@dataclass(frozen=True, slots=True)
class Comparison:
    classification: ComparisonClassification
    equivalent: bool
    details: JsonObject = field(default_factory=dict)


class Driver(Protocol):
    """Interface implemented by isolated old and new system drivers."""

    def invoke(self, request: DriverRequest) -> DriverResult:
        """Run one compatibility scenario without leaking implementation types."""
        ...
