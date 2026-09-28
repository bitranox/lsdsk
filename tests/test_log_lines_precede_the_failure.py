"""A log line written before a failure reaches stderr before the failure's own sentence.

lib_log_rich writes its console on a queue worker thread, while the sentence
reporting a failure - ``Error:`` and its ``Hint:``, or a traceback - is written
synchronously on the main thread. Nothing ordered the two, so the log line
describing a failure could land after the sentence it explains. Measured on
``lsdsk config-deploy --target app`` as an ordinary user: the ``ERRO`` line came
after ``Error:``/``Hint:`` in six runs of six, and the ``INFO`` line announcing
the deploy came after them too in one; ``lsdsk fail`` printed its traceback
ahead of the warning that precedes it in six of six.

The race is made observable rather than left to luck: the logging console here
takes :data:`SLOW_WRITE_SECONDS` to write each line, as a loaded terminal or a
slow pipe does, so a failure reported without waiting for the queue always wins
it. The console is injected at the application's own ``init_logging`` port and
writes through the same guarded stderr writer production uses.
"""

from __future__ import annotations

import io
import sys
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import lib_log_rich.runtime
import pytest
from lib_log_rich.domain import LogLevel
from lib_log_rich.runtime import RichConsoleAdapter

from lsdsk.adapters.cli import safe_console
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.cli.main import main
from lsdsk.composition import AppServices, build_production

if TYPE_CHECKING:
    from pathlib import Path

    from lib_layered_config import Config
    from lib_log_rich.application.ports.console import ConsolePort
    from lib_log_rich.domain import LogEvent
    from lib_log_rich.runtime import ConsoleAppearance

    from lsdsk.domain.deployment import DeployRequest

#: How long the console takes per line. Far longer than the microseconds between
#: logging a line and reporting the failure after it, so an unordered report
#: overtakes the line on every run rather than on some.
SLOW_WRITE_SECONDS = 0.2


class _SlowConsole:
    """The production console, taking :data:`SLOW_WRITE_SECONDS` over each line."""

    def __init__(self, inner: ConsolePort) -> None:
        self._inner = inner

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Wait, then write `event` as the real console would."""
        time.sleep(SLOW_WRITE_SECONDS)
        self._inner.emit(event, colorize=colorize)

    def flush(self) -> None:
        """Flush the real console."""
        self._inner.flush()


def _slow_console(appearance: ConsoleAppearance) -> ConsolePort:
    del appearance  # plain text on the guarded stderr writer, whatever was configured
    return _SlowConsole(
        RichConsoleAdapter(
            no_color=True,
            stream="custom",
            stream_target=safe_console.safe_stream(err=True, records_failures=True),
        )
    )


def _init_slow_logging(config: Config) -> None:
    """Start the logging runtime on a queue whose console is slow, as ``init_logging`` would."""
    del config
    lib_log_rich.runtime.init(
        lib_log_rich.runtime.RuntimeConfig(
            service="lsdsk-test",
            environment="test",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.CRITICAL,
            enable_ring_buffer=False,
            queue_enabled=True,
            console_adapter_factory=_slow_console,
        )
    )
    lib_log_rich.runtime.attach_std_logging()


def _refuse(request: DeployRequest) -> list[Path]:
    raise PermissionError(13, "Permission denied", "/etc/xdg/lsdsk")


def _run(argv: list[str], services: AppServices, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    """Run the real entry point with both streams captured, and hand back the code and stderr."""
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", stderr)
    code = main(argv, services_factory=lambda: services)
    return code, stderr.getvalue()


def _position(stderr: str, text: str) -> int:
    at = stderr.find(text)
    assert at >= 0, f"{text!r} never reached stderr: {stderr!r}"
    return at


@pytest.mark.os_agnostic
def test_a_refused_deploy_logs_its_failure_before_it_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    services = replace(build_production(), deploy_configuration=_refuse, init_logging=_init_slow_logging)

    code, stderr = _run(["config-deploy", "--target", "app"], services, monkeypatch)

    assert code == ExitCode.PERMISSION_DENIED, stderr
    announced = _position(stderr, "Deploying configuration")
    logged = _position(stderr, "Permission denied when deploying configuration")
    reported = _position(stderr, "Error: Permission denied")
    hinted = _position(stderr, "Hint:")
    assert announced < logged < reported < hinted, f"out of causal order: {stderr!r}"


@pytest.mark.os_agnostic
def test_a_crash_logs_what_preceded_it_before_the_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    services = replace(build_production(), init_logging=_init_slow_logging)

    code, stderr = _run(["fail"], services, monkeypatch)

    assert code == ExitCode.SOFTWARE_ERROR, stderr
    logged = _position(stderr, "Executing intentional failure command")
    crashed = _position(stderr, "I should fail")
    assert logged < crashed, f"the traceback overtook the log line before it: {stderr!r}"
