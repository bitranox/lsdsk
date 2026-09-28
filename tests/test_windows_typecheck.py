"""Windows and macOS are type-checked, which the shipped configuration cannot do.

``[tool.pyright]`` pins ``pythonPlatform = "Linux"`` so the check does not depend
on which runner executes it. Pyright then treats every ``sys.platform == "win32"``
or ``"darwin"`` branch as unreachable and never reads it, so a type error there is
invisible to the shipped check. Two arms close that, each a committed config over
the whole of ``src``: ``pyrightconfig.windows.json`` and ``pyrightconfig.darwin.json``.

Windows leaves out ``adapters/hw/linux``, whose POSIX-only symbols it reports as
missing - 18 errors, all in that one file, none elsewhere (measured 2026-09-28).
Darwin leaves out nothing. The Windows arm used to cover ``adapters/hw/windows``
alone, which left the Windows and macOS branches of ``default_history_path``
checked by nothing: a type error planted in either passed every check this
project ran, and turns this test red now. The transports ``reader.py`` and
``winapi.py`` are the other reason the Windows arm exists - no replayed capture
reaches them, so a type check is the only thing that reads them at all.

Pyright cross-checks platforms from any host, so none of this needs a Windows
or macOS machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest

ROOT = Path(__file__).parent.parent

#: The source tree every arm is scoped to.
SOURCE = ROOT / "src"

#: The modules that can ONLY be reached by a type check on Windows. A replayed
#: capture runs the builder and never these, so naming them here ties the arm to
#: the reason it exists rather than to a file count that moves.
TRANSPORTS = ("reader.py", "winapi.py")


class _Arm(NamedTuple):
    """One platform arm: its committed configuration, and what it must leave out.

    Attributes:
        config: The arm's own configuration, committed so a reader can run it by hand.
        platform: The ``pythonPlatform`` it must name.
        left_out: Source directories the arm excludes, relative to ``src``. Each is
            code that by construction never runs on that platform, and each must be
            named here, so a wider exclusion cannot slip in unseen.
    """

    config: Path
    platform: str
    left_out: tuple[str, ...]


#: Windows leaves out the Linux transport, whose POSIX-only symbols it reports as
#: missing (18 of them, all in that one file, measured 2026-09-28). Darwin leaves
#: out nothing: every POSIX symbol exists there.
ARMS = {
    "windows": _Arm(ROOT / "pyrightconfig.windows.json", "Windows", ("lsdsk/adapters/hw/linux",)),
    "darwin": _Arm(ROOT / "pyrightconfig.darwin.json", "Darwin", ()),
}


def _expected_files(arm: _Arm) -> list[Path]:
    """The source files the arm must read: all of ``src``, less what it leaves out."""
    skipped = [SOURCE / part for part in arm.left_out]
    return sorted(
        path for path in SOURCE.rglob("*.py") if not any(path.is_relative_to(directory) for directory in skipped)
    )


def _pyright(arm: _Arm) -> tuple[int, list[str]]:
    """Run one arm; return how many files it read and each error it found.

    The config's existence is asserted HERE rather than left to the sibling
    test: pyright given a ``--project`` that is not there falls back to the
    project's own configuration and reports the LINUX arm's clean result, which
    passed this test before the fallback was pinned.

    Args:
        arm: The arm to run.

    Returns:
        The number of files analysed, and one message per error.
    """
    assert arm.config.is_file(), f"{arm.config.name} is missing; pyright would silently fall back to the Linux arm"
    completed = subprocess.run(  # noqa: S603 - argv is built here, no shell
        [sys.executable, "-m", "pyright", "--pythonpath", sys.executable, "--project", str(arm.config), "--outputjson"],
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
    return summary["filesAnalyzed"], [
        f"{d.get('file', '')}: {d.get('message', '')}" for d in diagnostics if d.get("severity") == "error"
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", list(ARMS))
def test_each_arm_is_configured_for_its_platform_and_nothing_else(name: str) -> None:
    """The control that keeps an arm from degenerating into the Linux one.

    Every other assertion here passes just as well against a config that checks
    Linux, which is the one way this test could go quietly useless.
    """
    arm = ARMS[name]
    assert arm.config.is_file(), f"{arm.config.name} is missing, so the {arm.platform} arm does not exist"
    settings = json.loads(arm.config.read_text(encoding="utf-8"))
    assert settings["pythonPlatform"] == arm.platform, f"the arm is not checking {arm.platform}"
    assert settings["typeCheckingMode"] == "strict", "the arm is weaker than the shipped check"
    assert settings["include"] == ["src"], "the arm's scope moved away from the whole source tree"
    named_out = sorted(entry for entry in settings["exclude"] if entry.startswith("src/"))
    assert named_out == sorted(f"src/{part}" for part in arm.left_out), (
        f"the arm leaves out {named_out}, not the directories this test names"
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", list(ARMS))
def test_the_source_is_type_checked_on_each_platform_it_runs_on(name: str) -> None:
    """The claim itself, plus the control that it read the files it is about.

    The shipped check pins Linux, and pyright treats every ``sys.platform ==
    "win32"`` or ``"darwin"`` branch as unreachable there and never reads it. That
    left the Windows and macOS branches of ``default_history_path`` unchecked by
    anything, because the Windows arm used to cover ``adapters/hw/windows`` alone.
    """
    arm = ARMS[name]
    analysed, errors = _pyright(arm)

    # The scope, pinned to the tree's real contents. A count is the only thing
    # pyright reports on a CLEAN run - generalDiagnostics is empty - so an arm
    # that read the wrong set of files would otherwise read exactly like this one.
    expected = _expected_files(arm)
    if arm.platform == "Windows":
        names = {path.name for path in expected if "windows" in path.parts}
        assert set(TRANSPORTS) <= names, f"the transports are not in the Windows arm's scope: {sorted(names)}"
    assert analysed == len(expected), (
        f"the arm analysed {analysed} files but its scope holds {len(expected)}; "
        "a different number means it is not scoped where its configuration says"
    )

    assert not errors, f"the {arm.platform} arm has {len(errors)} type errors: {errors[:5]}"
