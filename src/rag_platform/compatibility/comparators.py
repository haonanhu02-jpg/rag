"""Capability-aware compatibility result comparators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isclose

from rag_platform.compatibility.contracts import (
    ComparatorKind,
    Comparison,
    ComparisonClassification,
    DriverResult,
    DriverStatus,
    JsonObject,
    JsonValue,
)


def compare_results(
    expected: DriverResult,
    actual: DriverResult,
    kind: ComparatorKind,
    options: JsonObject | None = None,
) -> Comparison:
    """Compare old and new results using the scenario's declared semantics."""
    settings = options or {}
    if actual.status is DriverStatus.NOT_IMPLEMENTED:
        return Comparison(
            ComparisonClassification.NOT_COMPARABLE,
            False,
            {"reason": "new_capability_not_implemented"},
        )
    if expected.status is not DriverStatus.SUCCEEDED or actual.status is not DriverStatus.SUCCEEDED:
        equivalent = expected.status is actual.status and expected.error == actual.error
        return _comparison(equivalent, {"status_match": equivalent})

    handlers = {
        ComparatorKind.EXACT: _exact,
        ComparatorKind.STRUCTURED: _structured,
        ComparatorKind.SET: _set,
        ComparatorKind.RANKING: _ranking,
        ComparatorKind.NUMERIC: _numeric,
        ComparatorKind.SEMANTIC: _semantic,
        ComparatorKind.STATE_SEQUENCE: _state_sequence,
        ComparatorKind.SECURITY_NEGATIVE: _security_negative,
    }
    equivalent, details = handlers[kind](expected.output, actual.output, settings)
    return _comparison(equivalent, details)


def _comparison(equivalent: bool, details: JsonObject) -> Comparison:
    classification = (
        ComparisonClassification.EQUIVALENT
        if equivalent
        else ComparisonClassification.REGRESSION
    )
    return Comparison(classification, equivalent, details)


def _exact(expected: JsonValue, actual: JsonValue, _: JsonObject) -> tuple[bool, JsonObject]:
    equivalent = expected == actual
    return equivalent, {"exact_match": equivalent}


def _structured(
    expected: JsonValue, actual: JsonValue, _: JsonObject
) -> tuple[bool, JsonObject]:
    equivalent = _contains(expected, actual)
    return equivalent, {"required_structure_present": equivalent}


def _contains(expected: JsonValue, actual: JsonValue) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _contains(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _contains(left, right) for left, right in zip(expected, actual, strict=True)
        )
    return expected == actual


def _as_object(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError("comparator requires object output")
    return value


def _as_sequence(value: JsonValue) -> Sequence[JsonValue]:
    if not isinstance(value, list):
        raise ValueError("comparator requires array output")
    return value


def _option_string(options: JsonObject, name: str, default: str) -> str:
    value = options.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _option_float(options: JsonObject, name: str, default: float) -> float:
    value = options.get(name, default)
    if not isinstance(value, int | float):
        raise ValueError(f"{name} must be numeric")
    return float(value)


def _set(expected: JsonValue, actual: JsonValue, options: JsonObject) -> tuple[bool, JsonObject]:
    field = _option_string(options, "field", "items")
    expected_items = _as_sequence(_as_object(expected)[field])
    actual_items = _as_sequence(_as_object(actual)[field])
    equivalent = {_canonical(item) for item in expected_items} == {
        _canonical(item) for item in actual_items
    }
    return equivalent, {"field": field, "set_match": equivalent}


def _ranking(
    expected: JsonValue, actual: JsonValue, options: JsonObject
) -> tuple[bool, JsonObject]:
    field = _option_string(options, "field", "ids")
    top_k = int(_option_float(options, "top_k", 3))
    minimum_overlap = _option_float(options, "minimum_overlap", 1.0)
    expected_ids = _as_sequence(_as_object(expected)[field])[:top_k]
    actual_ids = _as_sequence(_as_object(actual)[field])[:top_k]
    expected_set = {_canonical(item) for item in expected_ids}
    actual_set = {_canonical(item) for item in actual_ids}
    overlap = len(expected_set & actual_set)
    ratio = overlap / max(len(expected_ids), 1)
    equivalent = ratio >= minimum_overlap
    return equivalent, {"top_k": top_k, "overlap_ratio": ratio}


def _numeric(
    expected: JsonValue, actual: JsonValue, options: JsonObject
) -> tuple[bool, JsonObject]:
    field = _option_string(options, "field", "value")
    tolerance = _option_float(options, "absolute_tolerance", 0.0)
    expected_value = _as_object(expected)[field]
    actual_value = _as_object(actual)[field]
    if not isinstance(expected_value, int | float) or not isinstance(actual_value, int | float):
        raise ValueError("numeric comparator field must be numeric")
    difference = abs(float(expected_value) - float(actual_value))
    equivalent = isclose(float(expected_value), float(actual_value), abs_tol=tolerance)
    return equivalent, {"field": field, "absolute_difference": difference}


def _semantic(
    expected: JsonValue, actual: JsonValue, options: JsonObject
) -> tuple[bool, JsonObject]:
    minimum_field = _option_string(options, "minimum_field", "minimum_score")
    score_field = _option_string(options, "score_field", "score")
    minimum = _as_object(expected)[minimum_field]
    score = _as_object(actual)[score_field]
    if not isinstance(minimum, int | float) or not isinstance(score, int | float):
        raise ValueError("semantic comparator score must be numeric")
    equivalent = float(score) >= float(minimum)
    return equivalent, {"minimum": float(minimum), "score": float(score)}


def _state_sequence(
    expected: JsonValue, actual: JsonValue, options: JsonObject
) -> tuple[bool, JsonObject]:
    field = _option_string(options, "field", "states")
    expected_states = _as_sequence(_as_object(expected)[field])
    actual_states = _as_sequence(_as_object(actual)[field])
    equivalent = expected_states == actual_states
    return equivalent, {"field": field, "sequence_match": equivalent}


def _security_negative(
    expected: JsonValue, actual: JsonValue, _: JsonObject
) -> tuple[bool, JsonObject]:
    expected_denied = _as_object(expected).get("denied") is True
    actual_denied = _as_object(actual).get("denied") is True
    equivalent = expected_denied and actual_denied
    return equivalent, {"expected_denied": expected_denied, "actual_denied": actual_denied}


def _canonical(value: JsonValue) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
