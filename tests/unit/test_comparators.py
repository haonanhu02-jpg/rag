from __future__ import annotations

import pytest

from rag_platform.compatibility import (
    ComparatorKind,
    ComparisonClassification,
    DriverResult,
    DriverStatus,
    compare_results,
)
from rag_platform.compatibility.contracts import JsonObject, JsonValue


def _success(output: JsonValue) -> DriverResult:
    return DriverResult(status=DriverStatus.SUCCEEDED, output=output)


@pytest.mark.parametrize(
    ("kind", "expected", "actual", "options"),
    [
        (ComparatorKind.EXACT, {"value": 1}, {"value": 1}, None),
        (
            ComparatorKind.STRUCTURED,
            {"required": {"value": 1}},
            {"required": {"value": 1, "extra": True}, "new": "allowed"},
            None,
        ),
        (
            ComparatorKind.SET,
            {"items": ["a", "b"]},
            {"items": ["b", "a"]},
            None,
        ),
        (
            ComparatorKind.RANKING,
            {"ids": ["a", "b", "c"]},
            {"ids": ["a", "c", "x"]},
            {"top_k": 3, "minimum_overlap": 0.66},
        ),
        (
            ComparatorKind.NUMERIC,
            {"value": 1.0},
            {"value": 1.05},
            {"absolute_tolerance": 0.1},
        ),
        (
            ComparatorKind.SEMANTIC,
            {"minimum_score": 0.8},
            {"score": 0.85},
            None,
        ),
        (
            ComparatorKind.STATE_SEQUENCE,
            {"states": ["pending", "running", "succeeded"]},
            {"states": ["pending", "running", "succeeded"]},
            None,
        ),
        (
            ComparatorKind.SECURITY_NEGATIVE,
            {"denied": True},
            {"denied": True, "reason": "tenant_mismatch"},
            None,
        ),
    ],
)
def test_comparator_accepts_equivalent_result(
    kind: ComparatorKind,
    expected: JsonValue,
    actual: JsonValue,
    options: JsonObject | None,
) -> None:
    comparison = compare_results(
        _success(expected),
        _success(actual),
        kind,
        options,
    )

    assert comparison.equivalent is True
    assert comparison.classification is ComparisonClassification.EQUIVALENT


def test_comparator_rejects_security_regression() -> None:
    comparison = compare_results(
        _success({"denied": True}),
        _success({"denied": False}),
        ComparatorKind.SECURITY_NEGATIVE,
    )

    assert comparison.equivalent is False
    assert comparison.classification is ComparisonClassification.REGRESSION


def test_not_implemented_is_never_counted_as_compatible() -> None:
    comparison = compare_results(
        _success({"value": 1}),
        DriverResult(status=DriverStatus.NOT_IMPLEMENTED),
        ComparatorKind.EXACT,
    )

    assert comparison.equivalent is False
    assert comparison.classification is ComparisonClassification.NOT_COMPARABLE


def test_equal_errors_are_compatible() -> None:
    error: JsonObject = {"code": "invalid_filter"}
    comparison = compare_results(
        DriverResult(status=DriverStatus.FAILED, error=error),
        DriverResult(status=DriverStatus.FAILED, error=error),
        ComparatorKind.EXACT,
    )

    assert comparison.equivalent is True


@pytest.mark.parametrize(
    ("kind", "expected", "actual", "options", "message"),
    [
        (ComparatorKind.SET, [], [], None, "object output"),
        (ComparatorKind.SET, {"items": "bad"}, {"items": []}, None, "array output"),
        (
            ComparatorKind.SET,
            {"items": []},
            {"items": []},
            {"field": 1},
            "field must be a string",
        ),
        (
            ComparatorKind.RANKING,
            {"ids": []},
            {"ids": []},
            {"top_k": "bad"},
            "top_k must be numeric",
        ),
        (
            ComparatorKind.NUMERIC,
            {"value": "bad"},
            {"value": 1},
            None,
            "numeric comparator field",
        ),
        (
            ComparatorKind.SEMANTIC,
            {"minimum_score": "bad"},
            {"score": 1},
            None,
            "semantic comparator score",
        ),
    ],
)
def test_comparator_rejects_invalid_contract(
    kind: ComparatorKind,
    expected: JsonValue,
    actual: JsonValue,
    options: JsonObject | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        compare_results(_success(expected), _success(actual), kind, options)


def test_structured_comparator_rejects_wrong_array_shape() -> None:
    comparison = compare_results(
        _success([{"id": 1}]),
        _success([{"id": 1}, {"id": 2}]),
        ComparatorKind.STRUCTURED,
    )

    assert comparison.equivalent is False
