"""The claim the whole tool is sold on, pinned so it cannot quietly stop being true.

README, CLAUDE.md and ``skills/lsdsk/SKILL.md`` all state that lsdsk issues no
subprocesses and makes no network requests. A reader plans around it: it is why
the tool is safe to run on a wedged storage host where shelling out to
``smartctl`` would block, and why the skill instructs an agent to look figures up
itself rather than expect the tool to fetch them.

Nothing checked it. Every one of those documents would have gone on saying it
after the first ``import subprocess`` landed, so the claim is asserted here
against the import graph rather than against prose.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "lsdsk"

# Shelling out, and every stdlib route to a socket. Named individually rather
# than matched by prefix so that adding one is a deliberate act with a test
# failure attached, not something a wildcard silently absorbs.
FORBIDDEN_ROOTS = frozenset(
    {
        "subprocess",
        "socket",
        "socketserver",
        "ssl",
        # asyncio is deliberately absent: the TUI is built on Textual, which is
        # async, so an event loop is a legitimate thing for this code to reach
        # for and forbidding it would fight a change that never touches a socket.
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "webbrowser",
        "requests",
        "httpx",
        "urllib3",
        "aiohttp",
    }
)


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_roots(module: Path) -> set[str]:
    """Every top-level package name this module imports.

    Includes imports nested inside functions, which is where a shell-out would
    most plausibly appear: the project defers several imports to keep the fast
    path flat, so a top-of-file-only scan would miss exactly the shape it is
    looking for.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.os_agnostic
def test_the_scan_actually_reaches_every_module() -> None:
    """The control. A glob that matched nothing would make the checks below vacuous."""
    modules = _modules()
    assert len(modules) > 50, f"only {len(modules)} modules found under {SRC}"
    assert any(module.name == "reader.py" for module in modules), "the platform transports were not scanned"


@pytest.mark.os_agnostic
def test_the_scan_can_actually_see_a_forbidden_import() -> None:
    """The negative control, run against text rather than against the tree.

    Without this, a bug in the extractor would report a clean tree exactly as a
    clean tree does, and the two are indistinguishable from the result alone.
    """
    sample = ast.parse("def read():\n    import subprocess\n    from urllib import request\n")
    roots: set[str] = set()
    for node in ast.walk(sample):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots & FORBIDDEN_ROOTS == {"subprocess", "urllib"}


@pytest.mark.os_agnostic
def test_no_module_shells_out_or_opens_a_socket() -> None:
    """The claim itself, over every module including the deferred imports."""
    offenders = {
        str(module.relative_to(SRC)): sorted(found)
        for module in _modules()
        if (found := _imported_roots(module) & FORBIDDEN_ROOTS)
    }
    assert not offenders, (
        "lsdsk documents that it issues no subprocesses and makes no network requests, "
        f"but these modules import otherwise: {offenders}"
    )
