"""A logging timeout the platform cannot wait for must fall back, not end the run in a traceback.

``queue_stop_timeout`` is accepted when the logging runtime starts and only
FAILS when the shutdown's drain wait runs, in ``main()``'s ``finally``: a value
past ``threading.TIMEOUT_MAX`` (or infinite, or NaN) escaped as a raw
``OverflowError`` and exit 1, which reads as a drive verdict, and skipped the
stream restore. So the value is judged where it is read, and falls back to the
shipped one with the usual one-line warning, on every channel that can carry it.
"""

from __future__ import annotations

import sys
import threading

import lib_log_rich.runtime
import pytest

from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.cli.main import main
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

BEYOND_THE_PLATFORM = threading.TIMEOUT_MAX * 2

ARGV = ["--replay", "tests/fixtures/hw/linux-minimal.json", "--no-record", "findings"]


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> None:
    """The loader caches per process; drop it so each test reads its own environment."""
    get_config.cache_clear()


def _run(capfd: pytest.CaptureFixture[str], *before: str) -> tuple[int, str]:
    """Run ``main`` over a committed capture and return its code and standard error."""
    code = main([*before, *ARGV], services_factory=build_production)
    return code, " ".join(capfd.readouterr().err.split())


@pytest.mark.os_agnostic
def test_the_control_runs_without_a_warning(capfd: pytest.CaptureFixture[str]) -> None:
    """The unmodified run: the exit code the others must keep, and no logging warning."""
    code, said = _run(capfd)
    assert "lib_log_rich" not in said, said
    assert code in (0, 1), said


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["queue_stop_timeout", "queue_put_timeout"])
@pytest.mark.parametrize("value", ["1e18", "1e400", "inf", "nan", "9223372036854775808", "-1"])
def test_a_timeout_the_platform_cannot_wait_for_falls_back_through_set(
    capfd: pytest.CaptureFixture[str], key: str, value: str
) -> None:
    """The run ends as the control does, with one warning naming the key and no traceback."""
    control, _ = _run(capfd)
    get_config.cache_clear()
    code, said = _run(capfd, "--set", f"lib_log_rich.{key}={value}")

    assert code == control, f"left {code}: {said[-400:]}"
    assert f"Warning: ignoring lib_log_rich.{key}=" in said, said
    assert "Traceback" not in said, said
    assert "OverflowError" not in said, said


@pytest.mark.os_agnostic
@pytest.mark.parametrize("variable", ["LOG_QUEUE_STOP_TIMEOUT", "LOG_QUEUE_PUT_TIMEOUT"])
def test_a_timeout_the_platform_cannot_wait_for_falls_back_through_the_library_variable(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """The library reads these itself, ahead of every setting, so the variable is set aside and named."""
    control, _ = _run(capfd)
    get_config.cache_clear()
    monkeypatch.setenv(variable, "1e18")
    code, said = _run(capfd)

    assert code == control, f"left {code}: {said[-400:]}"
    assert f"Warning: ignoring {variable}=1e18" in said, said
    assert "Traceback" not in said, said


@pytest.mark.os_agnostic
@pytest.mark.parametrize("variable", ["LSDSK___LIB_LOG_RICH__QUEUE_STOP_TIMEOUT"])
def test_a_timeout_the_platform_cannot_wait_for_falls_back_through_the_environment_layer(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """The layered-config environment channel reaches the same fallback."""
    control, _ = _run(capfd)
    get_config.cache_clear()
    monkeypatch.setenv(variable, "1e18")
    code, said = _run(capfd)

    assert code == control, f"left {code}: {said[-400:]}"
    assert "Warning: ignoring lib_log_rich.queue_stop_timeout=" in said, said
    assert "Traceback" not in said, said


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", ["0", "0.5", "30"])
def test_a_timeout_the_platform_can_wait_for_is_used_without_a_warning(
    capfd: pytest.CaptureFixture[str], value: str
) -> None:
    """The bound refuses only what cannot work: zero (infinite wait) and sane waits pass."""
    code, said = _run(capfd, "--set", f"lib_log_rich.queue_stop_timeout={value}")
    assert "lib_log_rich" not in said, said
    assert code in (0, 1), said


def _failing_shutdown() -> None:
    """A logging shutdown that fails the way the drain wait did."""
    raise OverflowError("timestamp out of range for platform time_t")


@pytest.mark.os_agnostic
def test_a_logging_shutdown_that_fails_leaves_70_and_still_restores_the_streams(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The failure is reported as a crash and does not skip the flush and the stream restore after it."""
    streams = (sys.stdout, sys.stderr)
    try:
        code = main(ARGV, services_factory=build_production, shutdown_logging=_failing_shutdown)
        restored = (sys.stdout, sys.stderr)
    finally:
        if lib_log_rich.runtime.is_initialised():
            lib_log_rich.runtime.shutdown()

    said = capfd.readouterr().err
    assert code == ExitCode.SOFTWARE_ERROR, f"left {code}: {said[-300:]}"
    assert "OverflowError" in said, said
    assert restored == streams, "the streams were not put back"
