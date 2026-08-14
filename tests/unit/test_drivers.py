from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rag_platform.compatibility import (
    DriverRequest,
    DriverStatus,
    NotImplementedNewDriver,
    R2MinimumNewDriver,
    R3NewDriver,
    R4NewDriver,
    R5NewDriver,
    R6NewDriver,
    SubprocessDriver,
)
from rag_platform.compatibility.drivers import DriverProtocolError


def test_r0_new_driver_truthfully_reports_missing_capability() -> None:
    request = DriverRequest("CAP-27", "CAP-27-BASELINE", {"question": "What?"})

    result = NotImplementedNewDriver().invoke(request)

    assert result.status is DriverStatus.NOT_IMPLEMENTED
    assert result.error == {
        "code": "capability_not_implemented",
        "capability_id": "CAP-27",
        "scenario_id": "CAP-27-BASELINE",
    }


def test_subprocess_driver_round_trips_json_protocol() -> None:
    root = Path.cwd()
    driver = SubprocessDriver(
        [sys.executable, str(root / "scripts/new_system_driver.py")],
        cwd=root,
        environment={"PYTHONPATH": str(root / "src")},
    )

    result = driver.invoke(DriverRequest("CAP-01", "CAP-01-BASELINE"))

    assert result.status is DriverStatus.SUCCEEDED
    assert isinstance(result.output, dict)
    assert result.output["implementation_status"] == "implemented"


def test_r2_driver_reports_only_minimum_subset_evidence() -> None:
    driver = R2MinimumNewDriver()

    implemented = driver.invoke(DriverRequest("CAP-27", "CAP-27-BASELINE"))
    remaining = driver.invoke(DriverRequest("CAP-28", "CAP-28-BASELINE"))

    assert implemented.status is DriverStatus.SUCCEEDED
    assert isinstance(implemented.output, dict)
    assert implemented.output["implementation_status"] == "minimum_subset_implemented"
    assert remaining.status is DriverStatus.NOT_IMPLEMENTED


def test_r3_driver_truthfully_reports_full_and_foundation_statuses() -> None:
    driver = R3NewDriver()
    parsing = driver.invoke(DriverRequest("CAP-01", "CAP-01-BASELINE"))
    multimodal = driver.invoke(DriverRequest("CAP-35", "CAP-35-BASELINE"))
    remaining = driver.invoke(DriverRequest("CAP-28", "CAP-28-BASELINE"))
    assert isinstance(parsing.output, dict)
    assert parsing.output["implementation_status"] == "implemented"
    assert isinstance(multimodal.output, dict)
    assert multimodal.output["implementation_status"] == "parsing_foundation_implemented"
    assert remaining.status is DriverStatus.NOT_IMPLEMENTED


def test_r4_and_r5_drivers_overlay_only_evidenced_capabilities() -> None:
    r4 = R4NewDriver().invoke(DriverRequest("CAP-19", "CAP-19-BASELINE"))
    r5 = R5NewDriver().invoke(DriverRequest("CAP-27", "CAP-27-BASELINE"))
    future = R5NewDriver().invoke(DriverRequest("CAP-28", "CAP-28-BASELINE"))
    assert isinstance(r4.output, dict)
    assert r4.output["stage"] == "R4"
    assert isinstance(r5.output, dict)
    assert r5.output["implementation_status"] == "implemented"
    assert future.status is DriverStatus.NOT_IMPLEMENTED


def test_r6_driver_reports_complete_lifecycle_without_overclaiming_r7() -> None:
    lifecycle = R6NewDriver().invoke(DriverRequest("CAP-26", "CAP-26-BASELINE"))
    prior = R6NewDriver().invoke(DriverRequest("CAP-27", "CAP-27-BASELINE"))
    future = R6NewDriver().invoke(DriverRequest("CAP-28", "CAP-28-BASELINE"))
    assert isinstance(lifecycle.output, dict)
    assert lifecycle.output["implementation_status"] == "implemented"
    assert lifecycle.output["stage"] == "R6"
    assert prior.status is DriverStatus.SUCCEEDED
    assert future.status is DriverStatus.NOT_IMPLEMENTED


def test_subprocess_driver_reports_timeout(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(10)\n", encoding="utf-8")
    driver = SubprocessDriver([sys.executable, str(script)], timeout_seconds=0.01)

    result = driver.invoke(DriverRequest("CAP-01", "CAP-01-BASELINE"))

    assert result.status is DriverStatus.FAILED
    assert result.error is not None
    assert result.error["code"] == "driver_timeout"


def test_subprocess_driver_reports_process_failure(tmp_path: Path) -> None:
    script = tmp_path / "failed.py"
    script.write_text("import sys; print('reason', file=sys.stderr); raise SystemExit(7)\n")
    driver = SubprocessDriver([sys.executable, str(script)])

    result = driver.invoke(DriverRequest("CAP-01", "CAP-01-BASELINE"))

    assert result.status is DriverStatus.FAILED
    assert result.error is not None
    assert result.error["code"] == "driver_process_failed"
    assert result.error["returncode"] == 7


def test_subprocess_driver_rejects_invalid_protocol(tmp_path: Path) -> None:
    script = tmp_path / "invalid.py"
    script.write_text("print('not-json')\n", encoding="utf-8")
    driver = SubprocessDriver([sys.executable, str(script)])

    with pytest.raises(DriverProtocolError):
        driver.invoke(DriverRequest("CAP-01", "CAP-01-BASELINE"))


@pytest.mark.parametrize(
    "body",
    [
        "print('[]')\n",
        "print('{\"output\": null}')\n",
        'print(\'{"status": "succeeded", "error": []}\')\n',
    ],
)
def test_subprocess_driver_rejects_invalid_json_shapes(tmp_path: Path, body: str) -> None:
    script = tmp_path / "invalid_shape.py"
    script.write_text(body, encoding="utf-8")
    driver = SubprocessDriver([sys.executable, str(script)])

    with pytest.raises(DriverProtocolError):
        driver.invoke(DriverRequest("CAP-01", "CAP-01-BASELINE"))


def test_driver_result_is_json_serializable() -> None:
    result = NotImplementedNewDriver().invoke(
        DriverRequest("CAP-43", "CAP-43-BASELINE", {"probe": "temporal_rag"})
    )

    assert json.loads(json.dumps(result.as_json()))["status"] == ("not_implemented_in_new_repo")
