"""A pytest.raises block that asserts nothing cannot fail for the reason it was written.

``pytest.raises(X)`` is satisfied by ANY instance of X, including one the test's
own fixture caused. So the arm is green against the broken build and green
against the fixed one, and nothing about it looks wrong: the type is right, the
call is right, the block is short.

It has cost this repo three times - two vacuous arms in one session, and one
live instance found later by a reviewer - which is why the prose lesson was
judged not to have worked and this exists instead (user, 2026-09-21).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def _is_raises(call: ast.expr) -> bool:
    """Whether this context expression is a ``pytest.raises(...)`` call."""
    return isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "raises"


def _derived_from(name: str, inside: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every local name carrying what `name` holds, `name` included.

    A test routinely lifts the captured exception into a local first -
    ``said = str(refused.value)`` - and then asserts on THAT, so asking only
    about the captured name would report those arms as asserting nothing.

    Walked to a fixed point, because a name can be lifted twice.

    Args:
        name: The name the raises block bound.
        inside: The function to read.

    Returns:
        The names that carry it.
    """
    carried = {name}
    while True:
        grew = False
        for node in ast.walk(inside):
            if not isinstance(node, ast.Assign):
                continue
            mentions = {
                part.id
                for part in ast.walk(node.value)
                if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load)
            }
            if not (mentions & carried):
                continue
            for target in node.targets:
                for part in ast.walk(target):
                    if isinstance(part, ast.Name) and part.id not in carried:
                        carried.add(part.id)
                        grew = True
        if not grew:
            return carried


def _asserted_on(name: str, inside: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether what `name` holds reaches an ``assert`` in `inside`.

    Reading the name is not enough, and that is the whole point of this guard:
    ``with pytest.raises(X) as exc: ...`` followed by ``print(exc.value)`` reads
    it and still cannot fail for the reason the arm was written, which is the
    exact class this exists to catch.

    Args:
        name: The name the raises block bound.
        inside: The function to read.

    Returns:
        Whether an ``assert`` statement mentions it, or a name lifted from it.
    """
    carried = _derived_from(name, inside)
    return any(
        isinstance(node, ast.Assert)
        and any(
            isinstance(part, ast.Name) and part.id in carried and isinstance(part.ctx, ast.Load)
            for part in ast.walk(node)
        )
        for node in ast.walk(inside)
    )


def _unasserted_arms() -> tuple[list[str], int]:
    """Every raises block that asserts nothing about what it caught, and the total seen."""
    offenders: list[str] = []
    total = 0
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for block in ast.walk(function):
                if not isinstance(block, ast.With):
                    continue
                for item in block.items:
                    if not _is_raises(item.context_expr):
                        continue
                    total += 1
                    call = item.context_expr
                    assert isinstance(call, ast.Call)
                    if any(keyword.arg == "match" for keyword in call.keywords):
                        continue
                    bound = item.optional_vars
                    if isinstance(bound, ast.Name) and _asserted_on(bound.id, function):
                        continue
                    offenders.append(f"{path.relative_to(TESTS)}:{block.lineno}")
    return offenders, total


@pytest.mark.os_agnostic
def test_every_exception_arm_asserts_something_about_what_it_caught() -> None:
    """Either a match= pattern, or a capture the test goes on to read.

    Both forms are accepted because both are real assertions and neither suits
    every case: a message pattern pins what a caller is told, and a capture
    suits an exception whose CODE or field carries the claim - forcing match=
    on one of those would invent a sentence to satisfy a checker.

    What is not accepted is neither, because that arm passes on any instance of
    the type, from anywhere, including the fixture.
    """
    offenders, total = _unasserted_arms()

    assert total, "the control: no pytest.raises block was found at all, so this asserted nothing"
    assert not offenders, (
        f"{len(offenders)} of {total} pytest.raises blocks assert nothing about what was raised, "
        f"so they cannot fail for the reason they were written: {offenders}"
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("body", "expected", "why"),
    [
        pytest.param("assert exc.value.code == 2", True, "asserted directly", id="asserted directly"),
        pytest.param(
            "said = str(exc.value)\n    assert 'no' in said",
            True,
            "lifted once, then asserted",
            id="lifted once",
        ),
        pytest.param(
            "said = str(exc.value)\n    shown = said.lower()\n    assert 'no' in shown",
            True,
            "lifted twice, then asserted",
            id="lifted twice",
        ),
        pytest.param("print(exc.value)", False, "read but never asserted on", id="merely read"),
        pytest.param(
            "said = str(exc.value)\n    print(said)",
            False,
            "lifted and then only printed",
            id="lifted and printed",
        ),
        pytest.param("assert 1 == 1", False, "an assert that does not mention it", id="an unrelated assert"),
    ],
)
def test_the_predicate_can_answer_both_ways(body: str, expected: bool, why: str) -> None:
    """The guard's own control: it has to say NO to the shape it exists to catch.

    Before this, reading the bound name anywhere in the function counted as
    asserting on it, so `print(exc.value)` satisfied the guard while still being
    an arm that cannot fail for the reason it was written. A checker that
    answers YES to everything is indistinguishable from a clean suite.
    """
    source = f"def t():\n    with pytest.raises(ValueError) as exc:\n        go()\n    {body}\n"
    tree = ast.parse(source)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)

    assert _asserted_on("exc", function) is expected, f"{why}: got {not expected}"
