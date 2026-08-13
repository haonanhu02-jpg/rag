"""Compatibility driver implementations for R0 and later stages."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rag_platform.compatibility.contracts import (
    DriverRequest,
    DriverResult,
    DriverStatus,
    JsonObject,
)


class DriverProtocolError(RuntimeError):
    """Raised when an isolated driver violates the JSON protocol."""


@dataclass(frozen=True, slots=True)
class NotImplementedNewDriver:
    """Truthful R0 driver: no business capabilities exist in the new repository yet."""

    def invoke(self, request: DriverRequest) -> DriverResult:
        return DriverResult(
            status=DriverStatus.NOT_IMPLEMENTED,
            error={
                "code": "capability_not_implemented",
                "capability_id": request.capability_id,
                "scenario_id": request.scenario_id,
            },
        )


@dataclass(frozen=True, slots=True)
class R2MinimumNewDriver:
    """Report only R2 minimum subsets that have executable evidence."""

    implemented: frozenset[str] = frozenset(
        {
            "CAP-03",
            "CAP-04",
            "CAP-08",
            "CAP-10",
            "CAP-16",
            "CAP-21",
            "CAP-22",
            "CAP-23",
            "CAP-27",
            "CAP-38",
        }
    )

    def invoke(self, request: DriverRequest) -> DriverResult:
        if request.capability_id not in self.implemented:
            return NotImplementedNewDriver().invoke(request)
        return DriverResult(
            status=DriverStatus.SUCCEEDED,
            output={
                "capability_id": request.capability_id,
                "scenario_id": request.scenario_id,
                "implementation_status": "minimum_subset_implemented",
                "stage": "R2",
                "evidence": "tests/e2e/test_r2_minimum_rag.py",
            },
        )


@dataclass(frozen=True, slots=True)
class SubprocessDriver:
    """Invoke a JSON stdin/stdout driver in an isolated process."""

    command: Sequence[str]
    timeout_seconds: float = 30.0
    cwd: Path | None = None
    environment: Mapping[str, str] | None = None

    def invoke(self, request: DriverRequest) -> DriverResult:
        environment = os.environ.copy()
        if self.environment is not None:
            environment.update(self.environment)
        try:
            completed = subprocess.run(
                list(self.command),
                input=json.dumps(request.as_json(), ensure_ascii=False),
                cwd=self.cwd,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return DriverResult(
                status=DriverStatus.FAILED,
                error={"code": "driver_timeout", "timeout_seconds": self.timeout_seconds},
            )
        if completed.returncode != 0:
            return DriverResult(
                status=DriverStatus.FAILED,
                error={
                    "code": "driver_process_failed",
                    "returncode": completed.returncode,
                    "stderr": completed.stderr[-1000:],
                },
            )
        try:
            raw = json.loads(completed.stdout)
            if not isinstance(raw, dict):
                raise TypeError("driver result must be an object")
            status = DriverStatus(raw["status"])
            output = raw.get("output")
            error = raw.get("error")
            if error is not None and not isinstance(error, dict):
                raise TypeError("driver error must be an object or null")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DriverProtocolError("isolated driver returned invalid JSON") from exc
        return DriverResult(status=status, output=output, error=error)


@dataclass(frozen=True, slots=True)
class PinnedReferenceDriver:
    """R0 reference probe proving a checkout is exactly the locked old commit."""

    reference_root: Path
    expected_commit: str

    def invoke(self, request: DriverRequest) -> DriverResult:
        checkout_error = self._validate_checkout()
        if checkout_error is not None:
            return checkout_error
        commit = self._git("rev-parse", "HEAD")
        tree = self._git("show", "-s", "--format=%T", "HEAD")
        if request.scenario_id == "R0-REFERENCE-METADATA":
            output: JsonObject = {"commit": commit, "tree": tree, "dirty": False}
            return DriverResult(status=DriverStatus.SUCCEEDED, output=output)
        if request.scenario_id != f"{request.capability_id}-BASELINE":
            return DriverResult(
                status=DriverStatus.UNAVAILABLE,
                error={"code": "legacy_scenario_not_registered"},
            )
        matrix = self._git("show", "HEAD:docs/02-ragflow-capability-matrix.md")
        prefix = f"| {request.capability_id} |"
        matching_rows = [line for line in matrix.splitlines() if line.startswith(prefix)]
        if len(matching_rows) != 1:
            return DriverResult(
                status=DriverStatus.FAILED,
                error={
                    "code": "legacy_capability_evidence_missing",
                    "capability_id": request.capability_id,
                    "matches": len(matching_rows),
                },
            )
        row = matching_rows[0]
        output = {
            "commit": commit,
            "tree": tree,
            "capability_id": request.capability_id,
            "evidence_path": "docs/02-ragflow-capability-matrix.md",
            "evidence_row_sha256": hashlib.sha256(row.encode("utf-8")).hexdigest(),
            "probe": request.payload.get("probe"),
        }
        return DriverResult(status=DriverStatus.SUCCEEDED, output=output)

    def _validate_checkout(self) -> DriverResult | None:
        commit = self._git("rev-parse", "HEAD")
        dirty = bool(self._git("status", "--porcelain"))
        if commit != self.expected_commit or dirty:
            return DriverResult(
                status=DriverStatus.FAILED,
                error={
                    "code": "reference_checkout_mismatch",
                    "expected_commit": self.expected_commit,
                    "observed_commit": commit,
                    "dirty": dirty,
                },
            )
        return None

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.reference_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()
