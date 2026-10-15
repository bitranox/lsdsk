"""A platform-limited controller's drives are weighed once, and the helpers get the weights.

Three helpers decide whether the drives on a capped controller would notice a
faster seat and say how much they pull. Each took the controller and the
machine only to hand them to ``_drive_peaks``, so one finding walked the
drives on the controller up to three times and every helper carried a pair of
parameters it never read. The peaks are now taken once, where the finding is
built, and passed as the value the helpers actually read.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lsdsk.domain import diagnostics

HELPERS = ("_graded_by_the_drives", "_drives_would_notice", "_headroom_sentence")
BUILDER = "_platform_limited_finding"


def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(Path(diagnostics.__file__).read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _calls_to(node: ast.AST, name: str) -> int:
    return sum(
        1
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == name
    )


def test_the_helpers_take_the_drive_peaks_rather_than_the_machine() -> None:
    functions = _functions()
    for name in HELPERS:
        arguments = functions[name].args
        parameters = {argument.arg for argument in (*arguments.args, *arguments.kwonlyargs)}
        assert not parameters & {"controller", "inventory"}, f"{name} still takes {sorted(parameters)}"
        assert not _calls_to(functions[name], "_drive_peaks"), f"{name} weighs the drives again"


def test_the_finding_weighs_the_drives_once() -> None:
    assert _calls_to(_functions()[BUILDER], "_drive_peaks") == 1
