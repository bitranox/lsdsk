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
