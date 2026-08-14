from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from rag_platform.bootstrap.common import check_process
from rag_platform.bootstrap.maintainer import run as maintainer_run
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import ConfigurationError, Settings
from rag_platform.bootstrap.worker import run as worker_run


@pytest.mark.parametrize(
    "settings,match",
    [
        (Settings("unknown", "postgresql://localhost/db", "INFO"), "ENVIRONMENT"),
        (Settings("test", "sqlite:///db", "INFO"), "DATABASE_URL"),
        (Settings("test", "postgresql://localhost/db", "NOPE"), "LOG_LEVEL"),
    ],
)
def test_invalid_settings_fail_closed(settings: Settings, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        settings.validate()


def test_check_process_reports_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "unknown")
    assert check_process("api") == 1


def test_non_check_worker_and_maintainer_start_without_business_work() -> None:
    assert worker_run() == 0
    assert maintainer_run() == 0


def test_worker_and_maintainer_delegate_to_runtime() -> None:
    lifecycle_worker = MagicMock()
    lifecycle_reconciler = MagicMock()
    runtime = cast(
        R2Runtime,
        SimpleNamespace(
            lifecycle_worker=lifecycle_worker,
            lifecycle_reconciler=lifecycle_reconciler,
        ),
    )

    assert worker_run(runtime) == 0
    assert maintainer_run(runtime, dry_run=False) == 0
    lifecycle_worker.run_once.assert_called_once_with()
    lifecycle_reconciler.run.assert_called_once_with(dry_run=False)
