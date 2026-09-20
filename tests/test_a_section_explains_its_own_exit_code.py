"""A section that exits non-zero says why, on the page that exits.

The exit code counts every warning and critical in the MACHINE; a section shows
one part of it. So `lsdsk smart` printed 342 lines of attribute tables with no
severity marker anywhere and left `1`, and so did `slots`, `controllers`,
`trend`, `disks` and `health`. A person read a clean page and a failing exit; a
monitoring wrapper alerted on something the page could not explain.

Measured with `--set thresholds.wear_warning_percent=1`, the sharpest form of
it: four of those commands printed BYTE-IDENTICAL human output with and without
the override while the exit code moved from 0 to 1.

Closed by one line rather than by changing what the code means. Scoping the code
to each page would have silently stopped a monitor on `lsdsk health` from ever
catching a link fault the topology rules found, and nothing would have shown
that it had.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURE = "tests/fixtures/hw/linux-sas-hba.json"

#: Every section of the page, by the name the command is invoked under. `findings`
#: is not here: it lists every one of them, so it explains the code already.
SECTIONS = ("controllers", "disks", "health", "smart", "slots", "trend")

#: The three that already account for the code themselves: `topology` and the
#: whole-machine page both draw the PROBLEMS verdict block, and `findings` is the
#: list. Measured rather than assumed - a line here would be a second answer to a
#: question the page has already answered.
SELF_EXPLAINING = ("topology", "findings", "report")

#: A capture whose drives are all healthy, so the code is 0 and nothing is owed.
HEALTHY = "tests/fixtures/hw/linux-minimal.json"


def run(runner: CliRunner, factory: Callable[[], Any], *args: str) -> Any:
    """Invoke one command over a capture."""
    return runner.invoke(cli, [*args, "--replay", FIXTURE], obj=factory)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("section", SECTIONS)
def test_a_section_that_exits_non_zero_accounts_for_it_on_the_page(
    section: str, cli_runner: CliRunner, production_factory: Callable[[], Any], strip_ansi: Callable[[str], str]
) -> None:
    """The page a person reads has to explain the code the process leaves."""
    result = run(cli_runner, production_factory, section)
    assert result.exit_code == 1, f"{section} exits {result.exit_code}; this fixture is the one with findings"

    said = " ".join(strip_ansi(result.stdout).split())
    # On the SENTENCE, not on "lsdsk findings" alone: the verdict block already
    # ends with that phrase when it truncates, so a check for it passes on a page
    # that says nothing new.
    assert "are not on this page" in said, f"{section} leaves 1 and never accounts for it:\n{said[-400:]}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("section", SECTIONS)
def test_a_section_that_exits_zero_says_nothing_about_findings(
    section: str, cli_runner: CliRunner, production_factory: Callable[[], Any], strip_ansi: Callable[[str], str]
) -> None:
    """The control, and the half that keeps the line from being decoration.

    A machine with nothing wrong owes no explanation, so a line that appeared
    there would be noise on every clean run on every section.
    """
    result = cli_runner.invoke(cli, [section, "--replay", HEALTHY], obj=production_factory)
    assert result.exit_code == 0, f"{section} exits {result.exit_code} on the healthy capture"
    assert "are not on this page" not in strip_ansi(result.stdout), result.stdout[-400:]


@pytest.mark.os_agnostic
def test_the_line_names_how_many_and_of_what(
    cli_runner: CliRunner, production_factory: Callable[[], Any], strip_ansi: Callable[[str], str]
) -> None:
    """A pointer with no figure on it is a shrug.

    The counts come from the same tally the verdict line draws, so the two can
    never report one machine differently.
    """
    section = " ".join(strip_ansi(run(cli_runner, production_factory, "smart").stdout).split())
    page = " ".join(strip_ansi(run(cli_runner, production_factory, "report").stdout).split())

    assert "7 warnings" in section, section[-300:]
    assert "6 hints" in section, section[-300:]
    assert "7 warning" in page and "6 hint" in page, "the fixture's counts moved; both halves read the same tally"


@pytest.mark.os_agnostic
def test_the_page_that_lists_every_finding_does_not_point_elsewhere(
    cli_runner: CliRunner, production_factory: Callable[[], Any], strip_ansi: Callable[[str], str]
) -> None:
    """`findings` and the whole-machine page already show them.

    Without this the line would tell a reader looking at the full list to go and
    look at the full list.
    """
    for command in SELF_EXPLAINING:
        said = strip_ansi(run(cli_runner, production_factory, command).stdout)
        assert "are not on this page" not in said, f"{command} answers a question it has answered:\n{said[-300:]}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("section", SECTIONS)
def test_the_machine_readable_form_is_untouched(
    section: str, cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """The envelope already carries the findings, so prose there would be noise.

    Decoded rather than pattern-matched, because a line leaking into stdout is
    exactly what would break a caller and exactly what a substring check on the
    whole output would miss.
    """
    import json

    result = cli_runner.invoke(cli, [section, "--format", "json", "--replay", FIXTURE], obj=production_factory)
    envelope = json.loads(result.stdout)
    assert envelope["command"] == section
    assert "are not on this page" not in result.stdout
