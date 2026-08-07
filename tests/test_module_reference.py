"""The module reference and the command table match the code they describe.

``docs/systemdesign/module_reference.md`` opened by claiming to be generated from
the tree, so that "a module that exists is listed and a module that is listed
exists". It was hand-maintained, and eleven modules had accumulated that it did
not mention, along with two commands. Nothing could have noticed: a documentation
file has no other reader that fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
REFERENCE = ROOT / "docs" / "systemdesign" / "module_reference.md"
SRC = ROOT / "src"


def documented() -> str:
    return REFERENCE.read_text(encoding="utf-8")


def modules_on_disk() -> list[str]:
    """Every module the reference is expected to name.

    Package ``__init__.py`` files are excluded: they carry a layout docstring
    rather than behaviour, and listing fifteen of them would bury the modules a
    reader is looking for. The two that do hold code, the package surface and
    the composition root, are named explicitly.
    """
    named = {"src/lsdsk/__init__.py", "src/lsdsk/composition/__init__.py"}
    return sorted(
        # as_posix(), not str(): str() yields backslashes on Windows, so every
        # comparison against a documented "src/lsdsk/..." path failed there.
        path.relative_to(ROOT).as_posix()
        for path in SRC.rglob("*.py")
        if path.name != "__init__.py" or str(path.relative_to(ROOT)) in named
    )


@pytest.mark.os_agnostic
def test_the_scan_finds_a_realistic_number_of_modules() -> None:
    """The control: a broken glob would make both checks below pass trivially."""
    found = modules_on_disk()
    assert len(found) > 50, f"only {len(found)} modules found under {SRC}"
    assert "src/lsdsk/domain/models.py" in found


@pytest.mark.os_agnostic
def test_every_module_is_listed_in_the_reference() -> None:
    """The half that rots: a module added without a line here."""
    text = documented()
    missing = [module for module in modules_on_disk() if f"`{module}`" not in text]
    assert not missing, f"modules absent from {REFERENCE.name}: {missing}"


@pytest.mark.os_agnostic
def test_every_path_the_reference_lists_still_exists() -> None:
    """The other half: a module renamed or removed, leaving a dead line."""
    listed = re.findall(r"`(src/lsdsk/[^`]+\.py)`", documented())
    assert listed, "the control: no module paths were parsed out of the reference"
    stale = [path for path in listed if not (ROOT / path).is_file()]
    assert not stale, f"listed but absent from the tree: {stale}"


@pytest.mark.os_agnostic
def test_every_command_appears_in_the_command_table() -> None:
    """Read from the group's registry, so a command added later is covered unedited.

    Not by parsing ``--help``: that is laid out to the terminal width, and at a
    narrow one the wrapped prose matches a command-shaped regex, which a
    "more than ten found" control reads as success.
    """
    from lsdsk.adapters.cli import cli

    commands = sorted(cli.commands)
    assert len(commands) > 10, "the registry holds too few commands to be trusted"

    text = documented()
    missing = [command for command in commands if f"`{command}`" not in text]
    assert not missing, f"commands absent from the reference table: {missing}"
