"""The interface-shape claims CLAUDE.md makes, as checks rather than figures.

That block used to carry nine dated counts as the REASON each acceptance stood,
and six of them had gone stale - `(controller, inventory)` recorded at 9
signatures where there are now 21, the tree recorded at 77 modules where there
are now 91. The conclusions all survived a recount, but a reviewer who reads "9
signatures" and finds 21 has to re-argue an acceptance that was settled at 9.

So the figures are gone from the doc and the claims that are actually
INVARIANTS live here, where a change breaks a test instead of quietly making a
sentence untrue. The rest is re-derived on demand by
`scripts/interface_census.py`, which the doc names.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

ROOT = Path(__file__).resolve().parent.parent
CENSUS_PATH = ROOT / "scripts" / "interface_census.py"
SRC = ROOT / "src" / "lsdsk"


def _census_module() -> Any:
    """Load the census script by PATH, the way its sibling script is loaded.

    ``scripts/`` is not a package, so ``from interface_census import ...`` works
    under ``python -m pytest``, which puts the working directory on the path,
    and dies at collection under the bare ``pytest`` CI runs.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("interface_census", CENSUS_PATH)
    assert spec is not None and spec.loader is not None, "the census script is not where the tests expect it"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CENSUS: Final = _census_module()

#: How wide a signature has to be before the census lists it, read from the
#: script rather than repeated here, so the two cannot disagree.
WIDE: Final[int] = CENSUS.WIDE


@pytest.fixture(scope="module")
def figures() -> Any:
    """One census for every test here, as the script's own typed result."""
    return CENSUS.report(CENSUS.walk(SRC))


@pytest.mark.os_agnostic
def test_no_signature_of_this_project_s_own_is_six_parameters_wide(figures: Any) -> None:
    """The claim that was a sentence: the widest lists are a framework's.

    It was false when it was written - four of the six widest belonged to the
    deploy chain - and it is true now because that chain carries one request.
    Held here rather than left as prose, because ruff's own width rule is waived
    in five places (`adapters/cli/**`, `application/*`, `adapters/config/*`,
    `adapters/memory/*`, `windows/reader.py`) and a wide signature can only
    appear where the rule is not looking.

    A framework's width is the framework's: Click declares a parameter per
    option, and the Win32 bindings mirror call shapes that are wide by nature.
    """
    wide: list[Any] = [entry for entry in figures.wide_signatures if entry.owner == "lsdsk"]
    assert not wide, f"signatures this project owns at {WIDE}+ parameters: " + "; ".join(
        f"{entry.where} {entry.name} ({entry.width})" for entry in wide
    )


@pytest.mark.os_agnostic
def test_the_widest_signatures_that_do_exist_are_a_framework_s(figures: Any) -> None:
    """The control for the test above.

    With no wide signatures at all it would pass against a census that found
    nothing - a renamed marker, a broken walk - so at least one has to be there
    and be a framework's.
    """
    owners: dict[str, int] = figures.wide_by_owner
    assert owners, f"the census found no signature at all at {WIDE}+ parameters, so the check above asserts nothing"
    assert set(owners) <= {"click", "win32"}, owners


@pytest.mark.os_agnostic
def test_the_census_reads_the_whole_tree(figures: Any) -> None:
    """A second control: the walk must actually have read the source.

    Bounds rather than exact numbers, which is what the doc's own advice about
    a recaptured fixture says - an exact figure here would be the stale count
    this file exists to retire, moved one directory over.
    """
    modules: int = figures.modules
    functions: int = figures.functions
    assert modules > 50, modules
    assert functions > 300, functions
    assert functions > modules, (functions, modules)


@pytest.mark.os_agnostic
def test_the_script_the_doc_names_runs_and_emits_json() -> None:
    """The doc tells a reader to run it, so it has to run.

    Through a subprocess rather than by import, because what the doc names is a
    command line, and a module that imports cleanly can still fail as one.
    """
    result = subprocess.run(  # noqa: S603 - a fixed argv of this repo's own files
        [sys.executable, str(CENSUS_PATH), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-400:]

    emitted: dict[str, Any] = json.loads(result.stdout)
    assert emitted["modules"] > 50
    assert "wide_signatures" in emitted
