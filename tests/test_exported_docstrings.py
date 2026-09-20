"""The house docstring rule, asserted rather than trusted to review.

``CLAUDE.md`` requires Google-style docstrings with ``Args``, ``Returns`` and
``Raises`` on the exported surface. Ruff is configured with
``[tool.ruff.lint.pydocstyle] convention = "google"`` and does NOT select the
``D`` rules, so that setting enforces nothing: the convention is inert and the
requirement lived only in prose, which is why two thirds of the surface drifted
away from it without anything saying so.

One carve-out, and it is measured rather than stylistic. A ``click`` command's
docstring IS its ``--help`` text: with an ``Args:`` section added to
``cli_info``, ``lsdsk info --help`` printed

    Print resolved metadata so users can inspect installation details.
    Args:
    ctx: The click context carrying the services factory.
    output_format: Whether to print the human table or the JSON envelope.

un-indented, in the place a user reads to learn what the command does, and
describing a parameter no user can pass. So the rule stops at the command
boundary, and the second test below holds that boundary from the other side.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "lsdsk"

Func = ast.FunctionDef | ast.AsyncFunctionDef


def _exported(tree: ast.Module) -> set[str]:
    """Every name this module lists in ``__all__``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        listed = isinstance(node.value, (ast.List, ast.Tuple))
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets) or not listed:
            continue
        assert isinstance(node.value, (ast.List, ast.Tuple))
        names.update(e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return names


def _is_a_command(node: Func) -> bool:
    """Whether click turns this function into a command, making its docstring help text."""
    return any("command" in ast.unparse(d) or "group" in ast.unparse(d) for d in node.decorator_list)


def _takes_arguments(node: Func) -> bool:
    spec = node.args
    named = [p.arg for p in (*spec.posonlyargs, *spec.args, *spec.kwonlyargs) if p.arg not in {"self", "cls"}]
    return bool(named or spec.vararg or spec.kwarg)


def _returns_a_value(node: Func) -> bool:
    return node.returns is not None and ast.unparse(node.returns) != "None"


def _raises(node: Func) -> bool:
    return any(isinstance(n, ast.Raise) and n.exc is not None for n in ast.walk(node))


def _missing_sections(node: Func) -> list[str]:
    """Which Google sections this function owes and does not have."""
    doc = ast.get_docstring(node) or ""
    owed: list[str] = []
    if _takes_arguments(node) and "Args:" not in doc:
        owed.append("Args")
    if _returns_a_value(node) and "Returns:" not in doc:
        owed.append("Returns")
    if _raises(node) and "Raises:" not in doc:
        owed.append("Raises")
    return owed


def _exported_functions() -> list[tuple[Path, Func]]:
    found: list[tuple[Path, Func]] = []
    for module in sorted(SRC.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names = _exported(tree)
        found.extend(
            (module, node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if node.name in names
        )
    return found


@pytest.mark.os_agnostic
def test_the_scan_reaches_a_surface_worth_scanning() -> None:
    """The control. An empty walk would make both checks below pass vacuously."""
    found = _exported_functions()
    assert len(found) > 200, f"only {len(found)} exported functions found under {SRC}"
    assert any(_is_a_command(node) for _, node in found), "no click command was seen, so its carve-out is untested"
    assert any(not _is_a_command(node) for _, node in found), "no plain function was seen"


@pytest.mark.os_agnostic
def test_the_scan_can_actually_see_a_missing_section() -> None:
    """The negative control, against text rather than the tree.

    Without it, a bug in the extractor reports a clean surface exactly as a clean
    surface does, and the two are indistinguishable from the result alone.
    """
    bare = ast.parse('def f(a: int) -> int:\n    """Does a thing."""\n    raise ValueError(a)\n')
    node = bare.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _missing_sections(node) == ["Args", "Returns", "Raises"]

    full = ast.parse(
        "def f(a: int) -> int:\n"
        '    """Does a thing.\n\n    Args:\n        a: A number.\n\n'
        '    Returns:\n        It.\n\n    Raises:\n        ValueError: Always.\n    """\n'
        "    raise ValueError(a)\n"
    )
    node = full.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _missing_sections(node) == []


@pytest.mark.os_agnostic
def test_every_exported_function_documents_what_it_takes_returns_and_raises() -> None:
    """The house rule itself, over the whole exported surface."""
    offenders = {
        f"{module.relative_to(SRC)}:{node.lineno} {node.name}": owed
        for module, node in _exported_functions()
        if not _is_a_command(node) and (owed := _missing_sections(node))
    }
    assert not offenders, (
        f"{len(offenders)} exported functions are missing Google docstring sections CLAUDE.md requires: {offenders}"
    )


@pytest.mark.os_agnostic
def test_no_command_docstring_carries_a_google_section_because_it_is_the_help_text() -> None:
    """The carve-out, held from the other side so it cannot be undone by tidying.

    A future pass that "completes" the rule by adding ``Args:`` to the commands
    would put it straight into what a user reads, which is the measurement in
    this module's own docstring.
    """
    leaked = {
        f"{module.relative_to(SRC)}:{node.lineno} {node.name}": section
        for module, node in _exported_functions()
        if _is_a_command(node)
        for section in ("Args:", "Returns:", "Raises:")
        if section in (ast.get_docstring(node) or "")
    }
    assert not leaked, f"a command's docstring is its --help text, and these carry a Google section: {leaked}"
