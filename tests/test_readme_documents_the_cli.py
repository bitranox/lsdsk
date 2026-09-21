"""The README must name every command and every global option, and invent none.

Documentation drifts silently. `--report` was added, described in a paragraph,
and never reached a reference list, because there was no reference list; four
commands - `config`, `config-deploy`, `config-generate-examples`, `info` - had
never been listed at all. Nothing failed, because nothing was asking.

Both directions matter and they fail differently. A command the README omits is
a feature nobody finds. An option the README names that does not exist is worse:
a reader types it, gets a usage error, and concludes the tool is broken.

The command list comes from the group's own registry rather than from parsing
`--help`, which returned zero commands in a hand-written probe and then reported
"nothing missing" - a clean answer produced by checking nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

#: The document that OWNS each claim. The README is the front page and links to
#: these, so a claim is asserted against the file a reader is actually sent to;
#: pointing every test at the README instead would pass vacuously the moment a
#: section moves, which is how a whole reference can go missing with the suite
#: still green.
COMMANDS = ROOT / "COMMANDS.md"
PAGES = ROOT / "PAGES.md"

#: The README and the documents split out of it, which between them hold every
#: claim the README used to make on its own. INSTALL.md and CONFIG.md are NOT
#: here: they document uv's flags and POSIX file modes, where `--extra`, `--with`
#: and a `-r--r--r--` mode string would be read as invented lsdsk options.
_ENGLISH_DOCS = (README, COMMANDS, PAGES, ROOT / "REPORT.md", ROOT / "FINDINGS.md", ROOT / "WHY.md")

#: The German twins of those, which carry the same flags. An option string is
#: language-neutral, so a translator who renders one as prose or leaves a stale
#: one behind is caught by exactly the check that catches it in English.
USER_DOCS = (*_ENGLISH_DOCS, *(ROOT / "de" / doc.name for doc in _ENGLISH_DOCS))

# Neither is a feature. `fail` is the vehicle the traceback and exit-code tests
# drive through the real entry point, and `logdemo` previews the logging
# configuration; documenting them would invite somebody to run them.
TEST_VEHICLES = {"fail", "logdemo"}


def registered_commands() -> list[str]:
    """Every command, from the group that owns them."""
    from lsdsk.adapters.cli import cli

    commands = sorted(cli.commands)
    assert len(commands) > 10, f"the registry returned {len(commands)} commands, so this test is not testing"
    return commands


def help_of(*args: str) -> str:
    """One `--help` screen, at a width that does not wrap an option name."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "lsdsk", *args, "--help"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        # Added to the environment, not substituted for it: a fresh dict drops
        # SystemRoot on Windows, Winsock then fails, and the child dies with no
        # output at all - which reads here as "this CLI has no options".
        env={**os.environ, "COLUMNS": "200"},
    ).stdout


@pytest.mark.os_agnostic
def test_the_command_reference_names_every_command() -> None:
    """A command absent from the reference is one nobody discovers."""
    reference = COMMANDS.read_text(encoding="utf-8")
    missing = [
        command
        for command in registered_commands()
        if command not in TEST_VEHICLES and not re.search(rf"`lsdsk {re.escape(command)}\b", reference)
    ]

    assert not missing, f"{COMMANDS.name} documents no `lsdsk <command>` for: {missing}"


@pytest.mark.os_agnostic
def test_the_command_reference_names_every_global_option() -> None:
    """The global options are the ones a reader cannot discover from a command."""
    reference = COMMANDS.read_text(encoding="utf-8")
    options = sorted(set(re.findall(r"(--[a-z][a-z-]+)", help_of())) - {"--help"})
    assert len(options) > 5, f"only {len(options)} options parsed out of the group's help"

    missing = [option for option in options if option not in reference]

    assert not missing, f"{COMMANDS.name} does not mention: {missing}"


@pytest.mark.os_agnostic
def test_the_configuration_guide_lists_every_global_option() -> None:
    """CONFIG.md's own table of globals is where a reader goes for a setting.

    It is excluded from the invents-no-option sweep above for a good reason -
    it documents uv's flags and POSIX mode strings, which would read as
    invented lsdsk options - and the COMPLETENESS direction went unguarded with
    it. That is how `--tree-density` came to be absent from the one table a
    reader consults after reading the `[display]` key it belongs to, which sent
    them to edit a file for something a flag does one run at a time.

    Scoped to the TABLE rather than the document: prose elsewhere in the file
    is free to mention an option or not.
    """
    rows = _global_option_table()
    listed = {option for row in rows for option in re.findall(r"`(--[a-z][a-z-]+)", row)}
    options = sorted(set(re.findall(r"(--[a-z][a-z-]+)", help_of())) - {"--help"})
    assert len(options) > 5, f"only {len(options)} options parsed out of the group's help"
    assert len(listed) > 5, f"only {len(listed)} options parsed out of the table, so this asserted nothing"

    missing = [option for option in options if option not in listed]

    assert not missing, f"CONFIG.md's global-option table does not list: {missing}"


def _global_option_table() -> list[str]:
    """The rows of CONFIG.md's Global Options table, and nothing else.

    Read from the heading down to the first blank line after the table, so a
    later table of per-command options cannot satisfy this by accident.
    """
    lines = (ROOT / "CONFIG.md").read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "### Global Options")
    rows = [line for line in lines[start : start + 40] if line.startswith("| `--")]
    assert rows, "the Global Options table was not found where CONFIG.md puts it"
    return rows


@pytest.mark.os_agnostic
def test_the_documentation_invents_no_option() -> None:
    """An option the docs name but the CLI refuses reads as a broken tool."""
    documented = {
        option: doc.name
        for doc in USER_DOCS
        for option in re.findall(r"(--[a-z][a-z-]+)", doc.read_text(encoding="utf-8"))
    }
    every_screen = help_of() + "".join(help_of(command) for command in registered_commands())
    # Two controls. The first is the original: a flag that exists nowhere has
    # to be reported as invented. The second is what splitting the README made
    # necessary - every option left it in one move, and a scan over a file
    # holding none can no longer fail, so it has to be shown to have found some
    # before its silence means anything.
    assert "--not-a-real-flag" not in every_screen
    assert len(documented) > 5, f"only {len(documented)} options across {len(USER_DOCS)} documents"

    invented = {option: doc for option, doc in sorted(documented.items()) if option not in every_screen}

    assert not invented, f"documented options that do not exist: {invented}"


#: The prose that covers the page keys, which are eight bindings a reader meets
#: as one sentence rather than as eight lines.
PAGE_KEY_PROSE = "`1` to `8`"

#: Keys Textual names one way and a reader types another.
KEY_AS_TYPED = {"comma": ",", "full_stop": "."}


def _keys_as_typed(key_field: str) -> list[str]:
    """Every key one binding answers to, spelled the way a reader types it."""
    return [KEY_AS_TYPED.get(key, key) for key in key_field.split(",")]


def test_the_pages_document_names_a_key_for_every_action_the_interactive_view_binds() -> None:
    """A key the README omits is a feature reachable only by accident.

    The footer is not the manual: it lists what the page in front offers, and a
    key held back until its panel is scrollable never appears until a reader has
    already found the panel. Nothing was asking whether the README's key list
    still matched, and it had stopped: `i`, `shift+up`, `shift+down`, `,` and
    `.` were all bound and unmentioned, while a sentence beside them said
    nothing had to be selected, which six of the eight pages had stopped being
    true of.

    ONE key per action rather than every alias. `f9`, `f10` and `escape` are
    aliases nobody needs told about, so the claim asserted here is that every
    action is REACHABLE from the README, not that the README is a second copy of
    the binding table.
    """
    from lsdsk.adapters.tui.app import LsdskApp

    pages = PAGES.read_text(encoding="utf-8")
    assert PAGE_KEY_PROSE in pages, f"{PAGES.name} no longer says which keys switch page"

    unreachable = [
        f"{binding.action} ({binding.key})"
        for binding in LsdskApp.BINDINGS
        if not binding.action.startswith("show(")
        and not any(f"`{key}`" in pages for key in _keys_as_typed(binding.key))
    ]
    assert not unreachable, f"{PAGES.name} names no key for: " + ", ".join(unreachable)


@pytest.mark.os_agnostic
def test_the_published_long_description_has_no_link_a_reader_on_pypi_cannot_follow() -> None:
    """PyPI embeds this file verbatim; a relative target there resolves to nothing.

    The wheel's METADATA carries the file named by ``[project].readme`` with
    ``Description-Content-Type: text/markdown``, and pypi.org renders it at a URL
    that has no repository under it. So every relative target on the published
    page is dead - which was the whole Documentation table, both INSTALL deep
    links, the German switcher and the demo GIF the page opens with.

    Keyed on pyproject rather than on the name README.md, because the file that
    gets published is whichever one that key names.

    This does NOT bind the German README: it is not published to PyPI, and its
    relative links are what make it work in a checkout.
    """
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as handle:
        published = ROOT / tomllib.load(handle)["project"]["readme"]

    targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", published.read_text(encoding="utf-8"))
    assert targets, "the control: no link was found at all, so this asserted nothing"

    unreachable = sorted({t for t in targets if not t.startswith(("http://", "https://", "#", "mailto:"))})
    assert not unreachable, f"{published.name} carries targets pypi.org cannot resolve: {unreachable}"


@pytest.mark.os_agnostic
def test_every_repository_link_in_the_long_description_names_a_file_that_is_there() -> None:
    """Absolute is not the same as correct: a blob URL for a deleted file is a 404.

    Nothing offline can check a URL, so this checks the half that is knowable
    here - that each github.com/raw.githubusercontent target this repo points at
    names a path the repository actually tracks. The alternative is a page whose
    links all look right and half of which lead nowhere.
    """
    prefixes = (
        "https://github.com/bitranox/lsdsk/blob/main/",
        "https://raw.githubusercontent.com/bitranox/lsdsk/main/",
    )
    targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", README.read_text(encoding="utf-8"))
    own = [t for t in targets if t.startswith(prefixes)]
    assert own, "the control: no link points into this repository, so this asserted nothing"

    missing = sorted(
        relative
        for target in own
        for relative in [target.split("/main/", 1)[1].split("#", 1)[0]]
        if not (ROOT / relative).exists()
    )
    assert not missing, f"the README links to paths this repo does not have: {missing}"
