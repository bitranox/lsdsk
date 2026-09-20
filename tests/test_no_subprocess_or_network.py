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


# The kernel log is the one plausible SECOND source for the AHCI port bitmap,
# and it is a file read rather than an import, so the graph above cannot see it:
# a module that opened /dev/kmsg would import nothing at all. CLAUDE.md states
# that nothing here reads one, which is a claim about absence and therefore the
# kind that goes on reading true after it stops being.
KERNEL_LOG_SOURCES = frozenset({"dmesg", "journalctl", "/dev/kmsg", "/proc/kmsg", "klogctl"})


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The identity of every docstring node, so the scan below can skip them."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            found.add(id(first.value))
    return found


def _code_text(source: str) -> str:
    """Every string literal and identifier a module actually uses.

    Matched on the code rather than on the file's bytes because a comment or a
    docstring SAYING the tool does not read the kernel log is not a module that
    reads it, and a guard unable to tell those apart would refuse the very
    sentence that documents the rule.
    """
    tree = ast.parse(source)
    skip = _docstring_ids(tree)
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
    return "\n".join(parts)


def _kernel_log_mentions(source: str) -> set[str]:
    code = _code_text(source)
    return {name for name in KERNEL_LOG_SOURCES if name in code}


@pytest.mark.os_agnostic
def test_the_kernel_log_scan_can_see_one_and_still_lets_the_documentation_say_so() -> None:
    """The negative control, and the false-positive control beside it.

    The first arm is what makes a clean tree mean anything. The second is what
    keeps this guard from blocking the docstring that explains it, which is the
    way a text-matching guard usually fails.
    """
    reads_one = 'def read():\n    return open("/dev/kmsg").read()\n'
    only_says_it_does_not = 'def read():\n    """This never reads dmesg or journalctl."""\n    return None\n'
    assert _kernel_log_mentions(reads_one) == {"/dev/kmsg"}
    assert _kernel_log_mentions(only_says_it_does_not) == set()
    assert _kernel_log_mentions("def read():\n    return read_registers(device)\n") == set()


@pytest.mark.os_agnostic
def test_no_module_reads_the_kernel_log_for_what_a_register_would_not_give() -> None:
    """CLAUDE.md's AHCI paragraph, asserted rather than asserted-in-prose.

    A refused BAR5 map is recorded as a refusal and the port count is dropped.
    The kernel logging the same bitmap is history that validated the register
    once; a fallback to it would be a second source with different failure
    modes, and the document says there is none.
    """
    offenders = {
        str(module.relative_to(SRC)): sorted(found)
        for module in _modules()
        if (found := _kernel_log_mentions(module.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        f"CLAUDE.md states that no code here reads the kernel log, but these modules name one: {offenders}"
    )
