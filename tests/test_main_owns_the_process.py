"""`main()` is the process entry point, and everything it does is process-wide.

It restores the original streams, restores `lib_cli_exit_tools`' traceback
setting, and shuts the logging runtime down. All three are process globals, and
two of them were never guarded at all - so the one guard that existed, skipping
the logging shutdown off the main thread, was not thread safety. It was the
appearance of it on one act of three, untested in both branches, and its effect
was that a caller on a worker thread never flushed the logging runtime at all.

There is no such caller. `main` is not in `lsdsk.__all__`, it is reached from
`entry.py` and `__main__.py` which are main-thread by construction, and nothing
in the source threads. So the precondition is stated and the behaviour is the
same wherever it is called from, which is what these tests pin.
"""

from __future__ import annotations

import threading
from typing import Any

import lib_log_rich.runtime
import pytest

from lsdsk.adapters.cli.main import main
from lsdsk.composition import build_production


def run_and_report(argv: list[str], into: dict[str, Any]) -> None:
    """Run the CLI and record what it left behind."""
    into["code"] = main(argv, services_factory=build_production)
    into["logging_initialised"] = lib_log_rich.runtime.is_initialised()


@pytest.fixture(autouse=True)
def _leave_the_runtime_down() -> Any:
    """Put the logging runtime back down between tests.

    These tests are about a process-wide act, so one of them leaving the runtime
    up would decide the next one's answer.
    """
    yield
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.shutdown()


@pytest.mark.os_agnostic
def test_a_run_leaves_the_logging_runtime_down() -> None:
    """The baseline, on the main thread, and the control for the test below."""
    recorded: dict[str, Any] = {}
    run_and_report(["--replay", "tests/fixtures/hw/linux-minimal.json", "disks"], recorded)

    assert recorded["code"] in (0, 1), recorded
    assert recorded["logging_initialised"] is False, "the runtime was left up on the main thread"


@pytest.mark.os_agnostic
def test_a_run_on_a_worker_thread_leaves_it_down_too() -> None:
    """The guard's whole effect was that this did not happen.

    Skipping the shutdown off the main thread meant a caller there never flushed
    the logging runtime, while the same call restored the streams and the
    traceback setting for the whole process regardless. One act of three behaved
    differently, and nothing said so.
    """
    recorded: dict[str, Any] = {}
    worker = threading.Thread(
        target=run_and_report,
        args=(["--replay", "tests/fixtures/hw/linux-minimal.json", "disks"], recorded),
    )
    worker.start()
    worker.join(timeout=60)

    assert not worker.is_alive(), "the run did not finish, so nothing below is about the shutdown"
    assert recorded["code"] in (0, 1), recorded
    assert recorded["logging_initialised"] is False, "the runtime was left up on a worker thread"


@pytest.mark.os_agnostic
def test_the_runtime_really_does_come_up_from_this_wiring() -> None:
    """The control that keeps both tests above from passing vacuously.

    If the runtime never came up, "it is down afterwards" would be true of a
    `main()` that did nothing at all.

    Driven through the production wiring rather than by patching the setup
    module: composition binds `init_logging` into `AppServices` when the
    container is built, so an attribute patched afterwards is never consulted -
    the run would go on using the real one and the control would report on a
    call nobody made.
    """
    services = build_production()
    assert not lib_log_rich.runtime.is_initialised(), "the fixture left the runtime up"

    services.init_logging(services.get_config())
    assert lib_log_rich.runtime.is_initialised(), "this wiring does not bring the logging runtime up at all"
