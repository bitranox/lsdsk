"""The Windows platform is type-checked, which the shipped configuration cannot do.

``[tool.pyright]`` pins ``pythonPlatform = "Linux"`` so the check does not depend
on which runner executes it, and its comment records the trade: a Windows-only
type error in ``adapters/hw/windows`` is not caught. That trade was measured
again on 2026-09-20 and both halves of its reasoning had moved.

The numbers first. The comment says the Windows runner drowned in 31 POSIX-only
errors; it is 45 now, and every one sits in six files that by construction never
run on Windows - ``adapters/hw/linux/reader.py`` and five tests. ZERO are in
``adapters/hw/windows``.

The mitigation second, and this is the part that does not hold: it offers "the
captures replayed through its pure builder", which exercises ``builder.py``.
The gap is in ``reader.py`` and ``winapi.py``, the transports, which no replayed
capture reaches - they are omitted from coverage for exactly that reason, so a
type check is the ONLY thing that can ever read them.

Measured with a ``sys.platform == "win32"`` branch containing a type error,
planted in this package: the shipped Linux arm reports 0, because pyright treats
that branch as unreachable and never checks it, and this arm reports 1. Pyright
cross-checks platforms from any host, so none of this needs a Windows machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parent.parent

#: The arm's own configuration, committed so a reader can run it by hand.
CONFIG = ROOT / "pyrightconfig.windows.json"

#: The modules that can ONLY be reached by a type check. A replayed capture runs
#: the builder and never these, so naming them here ties the arm to the reason
#: it exists rather than to a file count that moves.
TRANSPORTS = ("reader.py", "winapi.py")


#: The package the arm is scoped to, and the source of the expected file count.
WINDOWS_PACKAGE = ROOT / "src" / "lsdsk" / "adapters" / "hw" / "windows"


def _pyright_windows() -> tuple[int, list[str]]:
    """Run the Windows arm; return how many files it read and each error it found.

    The config's existence is asserted HERE rather than left to the sibling
    test: pyright given a ``--project`` that is not there falls back to the
    project's own configuration and reports the LINUX arm's clean result, which
    passed this test before the fallback was pinned.

    Returns:
        The number of files analysed, and one message per error.
    """
    assert CONFIG.is_file(), f"{CONFIG.name} is missing; pyright would silently fall back to the Linux arm"
    completed = subprocess.run(  # noqa: S603 - argv is built here, no shell
        [sys.executable, "-m", "pyright", "--pythonpath", sys.executable, "--project", str(CONFIG), "--outputjson"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.stdout.strip(), f"pyright produced no report; stderr was: {completed.stderr[:400]}"
    report = cast("dict[str, Any]", json.loads(completed.stdout))
    summary = cast("dict[str, int]", report["summary"])
    diagnostics = cast("list[dict[str, str]]", report.get("generalDiagnostics", []))
    return summary["filesAnalyzed"], [d.get("message", "") for d in diagnostics if d.get("severity") == "error"]


@pytest.mark.os_agnostic
def test_the_windows_arm_is_configured_for_windows_and_nothing_else() -> None:
    """The control that keeps the arm from degenerating into the Linux one.

    Every other assertion here passes just as well against a config that checks
    Linux, which is the one way this test could go quietly useless.
    """
    assert CONFIG.is_file(), f"{CONFIG.name} is missing, so the Windows arm does not exist"
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert settings["pythonPlatform"] == "Windows", "the arm is not checking Windows"
    assert settings["typeCheckingMode"] == "strict", "the arm is weaker than the shipped check"
    assert settings["include"] == ["src/lsdsk/adapters/hw/windows"], (
        "the arm's scope moved; a wider one needs the POSIX-only exclusion list this scope avoids"
    )


@pytest.mark.os_agnostic
def test_the_windows_transports_are_type_checked_on_the_platform_they_run_on() -> None:
    """The claim itself, plus the control that it read the files it is about."""
    analysed, errors = _pyright_windows()

    # The scope, pinned to the package's real contents. A count is the only
    # thing pyright reports on a CLEAN run - generalDiagnostics is empty - so an
    # arm that had fallen back to the whole project would otherwise read exactly
    # like this one, at 154 files instead of these few.
    expected = sorted(path.name for path in WINDOWS_PACKAGE.glob("*.py"))
    assert set(TRANSPORTS) <= set(expected), f"the transports are not in {WINDOWS_PACKAGE}: found {expected}"
    assert analysed == len(expected), (
        f"the arm analysed {analysed} files but the package holds {len(expected)} ({expected}); "
        "a different number means it is not scoped where its configuration says"
    )

    assert not errors, f"the Windows adapter has {len(errors)} type errors: {errors[:5]}"
