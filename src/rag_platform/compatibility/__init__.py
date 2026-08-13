"""Compatibility harness used to prove capability parity with the pinned legacy system."""

from rag_platform.compatibility.comparators import compare_results
from rag_platform.compatibility.contracts import (
    ComparatorKind,
    Comparison,
    ComparisonClassification,
    Driver,
    DriverRequest,
    DriverResult,
    DriverStatus,
)
from rag_platform.compatibility.drivers import (
    NotImplementedNewDriver,
    PinnedReferenceDriver,
    R2MinimumNewDriver,
    R3NewDriver,
    SubprocessDriver,
)

__all__ = [
    "ComparatorKind",
    "Comparison",
    "ComparisonClassification",
    "Driver",
    "DriverRequest",
    "DriverResult",
    "DriverStatus",
    "NotImplementedNewDriver",
    "PinnedReferenceDriver",
    "R2MinimumNewDriver",
    "R3NewDriver",
    "SubprocessDriver",
    "compare_results",
]
