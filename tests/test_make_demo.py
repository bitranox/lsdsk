"""The README pictures name no version, so a release bump cannot make them stale.

The demo is recorded from the interactive view, whose title bar reads
``lsdsk <version>``. Pictures recorded that way read one version behind after
every bump, although nothing they show changed. The demo builds its apps
through one helper that keeps the title to the tool's name, and these tests
render a real frame through the script's own ``drive`` to hold that.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from lsdsk import __init__conf__
from lsdsk.adapters.hw.snapshot import load
from lsdsk.adapters.tui import LsdskApp

if TYPE_CHECKING:
    from types import ModuleType

    from lsdsk.domain.models import Inventory

ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURE: Final = ROOT / "tests" / "fixtures" / "hw" / "linux-sas-hba.json"
_ANSI: Final = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _make_demo() -> ModuleType:
    """Load the demo script by path, since ``scripts/`` is not a package.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("make_demo", ROOT / "scripts" / "make_demo.py")
    assert spec is not None and spec.loader is not None, "the demo script is not where the tests expect it"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MAKE_DEMO: Final = _make_demo()


async def _title_row(app: LsdskApp) -> str:
    """Drive one frame of ``app`` through the demo's own recorder and return its top row."""
    frames = await MAKE_DEMO.drive(app, (MAKE_DEMO.Step(note="one frame"),), MAKE_DEMO.Cast(), 0)
    return _ANSI.sub("", frames[0]).split("\n")[0]


@pytest.mark.asyncio
async def test_a_demo_frame_names_the_tool_and_no_version() -> None:
    machine: Inventory = load(FIXTURE)
    top = await _title_row(MAKE_DEMO.demo_app(machine))
    assert "lsdsk" in top
    assert __init__conf__.version not in top


@pytest.mark.asyncio
async def test_the_interactive_view_itself_still_names_its_version() -> None:
    """The control: the recorder does show a version, when the app carries one."""
    machine: Inventory = load(FIXTURE)
    top = await _title_row(LsdskApp(machine))
    assert f"lsdsk {__init__conf__.version}" in top
