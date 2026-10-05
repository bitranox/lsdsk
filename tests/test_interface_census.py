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


def _census_of(tmp_path: Path, sources: dict[str, str]) -> Any:
    """Run the real census over a synthetic tree laid out like this repository.

    Args:
        tmp_path: Where to build it.
        sources: Module path under ``src/lsdsk/`` to its source text.

    Returns:
        The census figures for exactly those modules.
    """
    package = tmp_path / "src" / "lsdsk"
    for relative, text in sources.items():
        module = package / relative
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(text, encoding="utf-8")
    return CENSUS.report(CENSUS.walk(package))


def _owners(figures: Any) -> dict[str, str]:
    """Each wide signature's name to whose shape the census filed it as."""
    return {entry.name: entry.owner for entry in figures.wide_signatures}


@pytest.mark.os_agnostic
def test_a_parameter_name_does_not_make_a_function_a_framework_s(tmp_path: Path) -> None:
    """Ownership is what the function IS: a Click command, or a Win32 binding.

    Deciding by parameter NAME filed any wide function of this project's that
    called one parameter ``ctx`` or ``handle`` as Click's or Win32's, so it
    escaped the six-wide invariant above. The two controls keep the real
    framework shapes recognised, so the fix cannot pass by calling everything
    this project's.
    """
    figures = _census_of(
        tmp_path,
        {
            "domain/planted.py": "def by_context(ctx, a, b, c, d, e): ...\ndef by_handle(handle, a, b, c, d, e): ...\n",
            "adapters/cli/planted.py": (
                "import click\n@click.command()\n@click.pass_context\ndef a_command(ctx, a, b, c, d, e): ...\n"
            ),
            "adapters/hw/windows/planted.py": "def a_binding(kernel32, handle, a, b, c, d): ...\n",
        },
    )
    assert _owners(figures) == {
        "by_context": "lsdsk",
        "by_handle": "lsdsk",
        "a_command": "click",
        "a_binding": "win32",
    }


@pytest.mark.os_agnostic
def test_star_args_and_star_kwargs_count_toward_a_signature_s_width(tmp_path: Path) -> None:
    """Four named parameters and two catch-alls is a six-wide signature."""
    figures = _census_of(tmp_path, {"domain/planted.py": "def spread(a, b, c, d, *rest, **extra): ...\n"})
    assert [(entry.name, entry.width) for entry in figures.wide_signatures] == [("spread", 6)]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "annotation",
    [
        "Tuple[int, int]",
        "typing.Tuple[int, int]",
        "tuple[int, int] | None",
        "Optional[tuple[int, int]]",
        "tuple[dict[str, int], dict[str, int]]",
        "tuple[tuple[int, int], ...]",
    ],
)
def test_every_spelling_of_a_same_typed_pair_is_seen(tmp_path: Path, annotation: str) -> None:
    """A same-typed anonymous return is the swap no checker catches, however it is written.

    ``Tuple`` and an optional pair were not read as tuples at all, and a member
    carrying commas of its own was split at them, so two identical members read
    as different ones. A variadic tuple OF pairs was skipped as "not a pair",
    while every element a caller unpacks from it is one.
    """
    source = f"import typing\nfrom typing import Optional, Tuple\ndef pair() -> {annotation}: ...\n"
    figures = _census_of(tmp_path, {"domain/planted.py": source})
    assert figures.anonymous_multi_value_returns == 1, annotation
    assert len(figures.same_typed_return_shapes) == 1, (annotation, figures.same_typed_return_shapes)


@pytest.mark.os_agnostic
def test_a_mixed_pair_is_counted_and_not_called_same_typed(tmp_path: Path) -> None:
    """The control for the spellings above: a pair the type checker DOES catch is left alone."""
    figures = _census_of(tmp_path, {"domain/planted.py": "def pair() -> tuple[dict[str, int], int] | None: ...\n"})
    assert figures.anonymous_multi_value_returns == 1
    assert figures.same_typed_return_shapes == {}


@pytest.mark.os_agnostic
def test_this_project_returns_no_same_typed_anonymous_tuple(figures: Any) -> None:
    """The acceptance CLAUDE.md records: every same-typed pair got a NamedTuple.

    Held rather than re-derived by hand, because the census that re-derives it
    is only run when somebody remembers to.
    """
    assert figures.anonymous_multi_value_returns > 0, "the control: the census found no multi-value return at all"
    assert figures.same_typed_return_shapes == {}, figures.same_typed_return_shapes


#: Two same-typed shapes whose bracketed members rich would read as markup tags,
#: plus a shape nested a level deeper, which is the one the real tree prints.
_BRACKETED_SHAPES: Final = (
    "def first() -> tuple[str, str]: ...\n"
    "def second() -> tuple[str, str]: ...\n"
    "def third() -> tuple[int, int]: ...\n"
    "def fourth() -> tuple[tuple[str, Cell], ...]: ...\n"
)


def _printed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    """Run the census's own ``main`` over a synthetic tree and return what it printed."""
    package = tmp_path / "src" / "lsdsk"
    package.mkdir(parents=True)
    (package / "planted.py").write_text(_BRACKETED_SHAPES, encoding="utf-8")
    monkeypatch.setattr(CENSUS, "SRC", package)
    monkeypatch.setattr(sys, "argv", ["interface_census.py", *argv])
    assert CENSUS.main() == 0
    return capsys.readouterr().out


@pytest.mark.os_agnostic
def test_a_shape_survives_the_printed_report_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A shape is printed as written, not as rich's markup parser leaves it.

    The report printed ``tuple[tuple[str, Cell], ...]`` as ``tuple``, because
    rich read ``[str, Cell]`` as a style tag and dropped it.
    """
    printed = _printed(tmp_path, monkeypatch, capsys)
    assert "('tuple[str, str]', 2)" in printed, printed
    assert "'tuple[int, int]': 1" in printed, printed


@pytest.mark.os_agnostic
def test_two_shapes_stay_two_keys_in_the_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Eaten markup made two different shapes one JSON key, so one count overwrote the other."""
    emitted: dict[str, Any] = json.loads(_printed(tmp_path, monkeypatch, capsys, "--json"))
    assert emitted["same_typed_return_shapes"] == {"tuple[str, str]": 2, "tuple[int, int]": 1}
    assert emitted["largest_return_shape"] == ["tuple[str, str]", 2]
