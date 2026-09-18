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

README = Path(__file__).resolve().parent.parent / "README.md"

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
def test_the_readme_names_every_command() -> None:
    """A command absent from the README is one nobody discovers."""
    readme = README.read_text(encoding="utf-8")
    missing = [
        command
        for command in registered_commands()
        if command not in TEST_VEHICLES and not re.search(rf"`lsdsk {re.escape(command)}\b", readme)
    ]

    assert not missing, f"the README documents no `lsdsk <command>` for: {missing}"


@pytest.mark.os_agnostic
def test_the_readme_names_every_global_option() -> None:
    """The global options are the ones a reader cannot discover from a command."""
    readme = README.read_text(encoding="utf-8")
    options = sorted(set(re.findall(r"(--[a-z][a-z-]+)", help_of())) - {"--help"})
    assert len(options) > 5, f"only {len(options)} options parsed out of the group's help"

    missing = [option for option in options if option not in readme]

    assert not missing, f"the README does not mention: {missing}"


@pytest.mark.os_agnostic
def test_the_readme_invents_no_option() -> None:
    """An option the README names but the CLI refuses reads as a broken tool."""
    readme = README.read_text(encoding="utf-8")
    every_screen = help_of() + "".join(help_of(command) for command in registered_commands())
    # A control, because the check is only worth its runtime if it can fire: a
    # flag that exists nowhere has to be reported as invented.
    assert "--not-a-real-flag" not in every_screen

    invented = [option for option in sorted(set(re.findall(r"(--[a-z][a-z-]+)", readme))) if option not in every_screen]

    assert not invented, f"the README documents options that do not exist: {invented}"


#: The prose that covers the page keys, which are eight bindings a reader meets
#: as one sentence rather than as eight lines.
PAGE_KEY_PROSE = "`1` to `8`"

#: Keys Textual names one way and a reader types another.
KEY_AS_TYPED = {"comma": ",", "full_stop": "."}


def _keys_as_typed(key_field: str) -> list[str]:
    """Every key one binding answers to, spelled the way a reader types it."""
    return [KEY_AS_TYPED.get(key, key) for key in key_field.split(",")]


def test_the_readme_names_a_key_for_every_action_the_interactive_view_binds() -> None:
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

    readme = README.read_text(encoding="utf-8")
    assert PAGE_KEY_PROSE in readme, "the README no longer says which keys switch page"

    unreachable = [
        f"{binding.action} ({binding.key})"
        for binding in LsdskApp.BINDINGS
        if not binding.action.startswith("show(")
        and not any(f"`{key}`" in readme for key in _keys_as_typed(binding.key))
    ]
    assert not unreachable, "the README names no key for: " + ", ".join(unreachable)
