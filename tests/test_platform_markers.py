"""The platform markers actually skip, and every registered one is wired.

Registering a marker under ``[tool.pytest.ini_options]`` only silences the
unknown-marker warning. Without a ``pytest_runtest_setup`` hook acting on it, a
marked test reads as guarded in the source and runs on every platform anyway.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest
from conftest import PLATFORM_MARKERS

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


@pytest.mark.os_windows
def test_a_marker_for_another_platform_does_not_run_here() -> None:
    """The proof, driven rather than asserted.

    This body fails outright. On a non-Windows machine the hook must skip it, so
    a green run IS the evidence that the marker is wired; if the wiring were
    removed this test would go red on every Linux and macOS runner rather than
    quietly passing. On Windows it is genuinely selected, where the assertion
    below is the true statement instead.
    """
    assert sys.platform == "win32", "an os_windows test ran on a machine that is not Windows"


@pytest.mark.os_agnostic
def test_every_registered_platform_marker_is_wired_to_a_rule() -> None:
    """A marker added to pyproject but not to the hook would skip nothing."""
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    registered = {
        entry.split(":", 1)[0]
        for entry in declared["tool"]["pytest"]["ini_options"]["markers"]
        if entry.startswith("os_")
    }
    # os_agnostic asserts the test runs everywhere, so it has nothing to skip on.
    needing_a_rule = registered - {"os_agnostic"}
    assert needing_a_rule, "the control: no platform markers were found to check"
    assert needing_a_rule == set(PLATFORM_MARKERS), (
        f"registered but not wired: {sorted(needing_a_rule - set(PLATFORM_MARKERS))}; "
        f"wired but not registered: {sorted(set(PLATFORM_MARKERS) - needing_a_rule)}"
    )


@pytest.mark.os_agnostic
def test_each_rule_admits_exactly_the_platform_it_names() -> None:
    """A rule that is true everywhere skips nothing and is worse than none."""
    expected = {
        "os_posix": {"linux", "darwin"},
        "os_linux": {"linux"},
        "os_macos": {"darwin"},
        "os_windows": {"win32"},
    }
    for marker, is_supported in PLATFORM_MARKERS.items():
        admitted = {platform for platform in ("linux", "darwin", "win32") if is_supported(platform)}
        assert admitted == expected[marker], f"{marker} admits {sorted(admitted)}"


def test_no_test_seeds_the_linux_config_home_itself() -> None:
    r"""A test that sets `XDG_CONFIG_HOME` is a Linux test whatever marker it wears.

    macOS reads ``~/Library/Application Support/<vendor>/<app>`` and Windows
    reads ``%APPDATA%\<vendor>\<app>``, so a file seeded under
    ``XDG_CONFIG_HOME`` is invisible there and the run loads the shipped
    defaults instead. The assertion above it then passes or fails for a reason
    that has nothing to do with what it meant to test.

    Four files did this at once while carrying `os_posix`, which includes macOS,
    and between them they reddened ten cells on every macOS runner: a malformed
    file that produced no error and left `0` where `78` was asserted, deployment
    modes read off a directory nothing wrote, a profile that resolved to the
    shipped 24 instead of the seeded 9, and a config-file warning that never
    fired while its own silent control passed. None of it was visible here.

    So the variable is set in ONE place - `conftest._user_config_dir`, which
    branches on the platform - and this refuses a second copy. Prose did not
    hold it; the same mistake was made four times.
    """
    import re
    from pathlib import Path

    tests_dir = Path(__file__).parent
    offenders: list[str] = []
    # A real seeding, not a mention: the name passed to an env-setting call.
    # The variable's name is assembled rather than written, so this file does not
    # contain the token it sweeps for. Spelled out, the guard matched its own
    # control and reported itself as the offender - a scanner has to stay out of
    # its own corpus.
    variable = "XDG_" + "CONFIG_HOME"
    seeding = re.compile(rf"""(setenv|environ\[|environ\.setdefault)\s*\(?\s*["']{variable}["']""")
    for source in sorted(tests_dir.rglob("*.py")):
        if source.name == "conftest.py":
            continue
        if seeding.search(source.read_text(encoding="utf-8")):
            offenders.append(str(source.relative_to(tests_dir)))

    assert not offenders, (
        "these seed the Linux config home directly, so they cannot hold on macOS or Windows; "
        f"use the user_config_dir fixture instead: {offenders}"
    )

    # The control: the pattern must be able to find one, built the same way.
    assert seeding.search(f'monkeypatch.setenv("{variable}", str(root))'), (
        "the control: this pattern cannot recognise a real seeding, so the sweep above asserts nothing"
    )
