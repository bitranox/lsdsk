"""Output must fit the terminal it is actually running in.

Every table already fits itself by dropping its lowest-priority columns. Prose
does not: a line printed with a plain echo keeps its own length and runs off the
side of a narrow terminal, which is how the counter legend shipped at 111
characters onto a 60-column screen.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":  # pragma: no cover - a pty is POSIX only
    # Skipped rather than guarded per-test, so the POSIX-only imports below sit
    # in a branch the type checker knows Windows never reaches. Marking each
    # test os_posix stops them RUNNING there; it does not stop pyright reading
    # the module under --pythonplatform Windows.
    pytest.skip("terminal width checks need a pty", allow_module_level=True)

import fcntl
import pty
import termios

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba-later.json"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run_on_a_terminal(columns: int, *args: str) -> str:
    """Run lsdsk attached to a real pseudo-terminal of a given width.

    A CliRunner cannot answer this question: it is not a terminal, so the code
    takes the redirected-output branch and the real width is never consulted.
    """
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, columns, 0, 0))
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "lsdsk", *args],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.STDOUT,
        env={**os.environ, "TERM": "xterm", "COLUMNS": str(columns)},
        close_fds=True,
    )
    os.close(slave)
    chunks: list[bytes] = []
    try:
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        process.wait()
        os.close(master)
    return ANSI.sub("", b"".join(chunks).decode("utf-8", "replace"))


@pytest.mark.os_posix
@pytest.mark.parametrize("columns", [40, 60, 80, 100, 120, 200])
@pytest.mark.parametrize("command", ["health", "trend", "findings", "disks"])
def test_nothing_runs_off_the_side_of_the_terminal(columns: int, command: str) -> None:
    text = run_on_a_terminal(columns, command, "--replay", str(FIXTURE))
    assert text.strip(), "no output, so this proves nothing"
    too_long = [line.rstrip() for line in text.splitlines() if len(line.rstrip()) > columns]
    assert not too_long, f"{len(too_long)} line(s) exceed {columns} columns: {too_long[:1]}"


@pytest.mark.os_posix
def test_a_narrow_terminal_drops_columns_rather_than_wrapping_rows() -> None:
    """The table adapts, which is what makes the width detection worth having."""
    narrow = run_on_a_terminal(60, "health", "--replay", str(FIXTURE))
    wide = run_on_a_terminal(200, "health", "--replay", str(FIXTURE))

    def columns_of(text: str) -> list[str]:
        return next(line.split() for line in text.splitlines() if "device" in line)

    assert len(columns_of(narrow)) < len(columns_of(wide))
    assert "crc" in columns_of(narrow), "the highest-priority counter must survive"


@pytest.mark.os_posix
def test_a_real_terminal_width_is_used_rather_than_the_piped_default() -> None:
    """A 200-column terminal must not be laid out as if it were the piped 120."""
    assert max(len(x.rstrip()) for x in run_on_a_terminal(200, "smart", "--replay", str(FIXTURE)).splitlines()) > 120


@pytest.mark.os_agnostic
def test_no_width_strands_a_severity_marker_on_its_own_line() -> None:
    """The marker is the only thing on the row that says the row is a problem.

    It used to be appended after the fitted columns, and the controller line had
    no width budget at all, so on a narrow terminal it wrapped onto a line of
    its own and detached from what it marks. Cropping instead would have removed
    the marker rather than the data, so it leads the row now, as it already does
    in every table. Measured before: every flagged row at COLUMNS=40 on three
    real fixtures, plus 42 of 181 other widths wherever the fit landed on the
    boundary.
    """
    import io
    import json

    from rich.console import Console

    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.report import render_tree
    from lsdsk.adapters.render.theme import SEVERITY_MARKERS
    from lsdsk.domain.diagnostics import diagnose

    fixtures = sorted((Path(__file__).parent / "fixtures" / "hw").glob("*.json"))
    assert fixtures, "no fixtures found, so this asserted nothing"
    alone = set(SEVERITY_MARKERS.values())
    flagged_seen = 0
    stranded: list[str] = []
    for fixture in fixtures:
        machine = build_from(json.loads(fixture.read_text(encoding="utf-8")))
        findings = diagnose(machine)
        flagged_seen += len(findings)
        for width in range(20, 201):
            buffer = io.StringIO()
            Console(file=buffer, width=width, no_color=True).print(render_tree(machine, findings, width=width))
            stranded += [
                f"{fixture.name} at width {width}" for line in buffer.getvalue().splitlines() if line.strip() in alone
            ]
    assert flagged_seen, "no fixture produced a finding, so no marker was ever rendered"
    assert not stranded, f"marker stranded on its own line: {stranded[:5]} ({len(stranded)} total)"
