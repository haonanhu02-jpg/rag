from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rag_platform.compatibility import (
    DriverRequest,
    DriverStatus,
    NotImplementedNewDriver,
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

    assert result.status is DriverStatus.NOT_IMPLEMENTED
    assert result.error is not None
    assert result.error["capability_id"] == "CAP-01"


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
        "print('{\"status\": \"succeeded\", \"error\": []}')\n",
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

    assert json.loads(json.dumps(result.as_json()))["status"] == (
        "not_implemented_in_new_repo"
    )
