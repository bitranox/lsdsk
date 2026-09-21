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


def _reads(name: str, inside: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether `name` is ever READ in `inside`, rather than only bound."""
    return any(
        isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load) for node in ast.walk(inside)
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
                    if isinstance(bound, ast.Name) and _reads(bound.id, function):
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
