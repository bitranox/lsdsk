"""COMMANDS.md names exactly the ``error.type`` values a failure envelope can carry.

A caller branches on ``error.type``, so the list it reads is a contract. The
list once named two types no envelope ever carries - a crash writes none, and 1
is a verdict - and left out ``IO_ERROR``, which every failed write emits. The
set the code can produce is read from the code itself: every exit code a call
to ``fail`` or ``_refuse_before_the_run`` names, plus the code click gives a
usage error, which reaches the envelope through ``main``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import click

from lsdsk.adapters.cli.exit_codes import error_type_for

ROOT = Path(__file__).resolve().parent.parent
CLI_SOURCE = ROOT / "src" / "lsdsk" / "adapters" / "cli"

# The two functions through which a command chooses the code its envelope names.
_CODE_CHOOSERS = frozenset({"fail", "_refuse_before_the_run"})
_DOCUMENTED_LIST = re.compile(r"The `type` is the exit code's own\s+name - (?P<names>.+?) - so it", re.S)


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _codes_named_in(call: ast.Call) -> set[str]:
    named: set[str] = set()
    for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
        for node in ast.walk(argument):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ExitCode":
                named.add(node.attr)
    return named


def _types_the_code_can_emit() -> set[str]:
    emitted: set[str] = set()
    for source in CLI_SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _called_name(node) in _CODE_CHOOSERS:
                emitted |= _codes_named_in(node)
    emitted.add(error_type_for(click.UsageError("x").exit_code))
    return emitted


def _types_commands_md_names() -> set[str]:
    text = (ROOT / "COMMANDS.md").read_text(encoding="utf-8")
    match = _DOCUMENTED_LIST.search(text)
    assert match is not None, "COMMANDS.md no longer carries the sentence listing the error types"
    return set(re.findall(r"`([A-Z_]+)`", match.group("names")))


def test_commands_md_names_every_error_type_and_only_those() -> None:
    emitted = _types_the_code_can_emit()
    documented = _types_commands_md_names()

    # Neither side may be empty: a scan that found no call site would agree
    # with a sentence that names nothing.
    assert {"CONFIG_ERROR", "IO_ERROR"} <= emitted
    assert documented
    assert documented == emitted
