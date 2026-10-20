"""The claim the whole tool is sold on, pinned so it cannot quietly stop being true.

README, CLAUDE.md and ``skills/lsdsk/SKILL.md`` all state that lsdsk shells out
to nothing and makes no network requests, except one consent-gated path:
installing uv when the reader asks. A reader plans around it: it is why the tool
is safe to run on a wedged storage host where shelling out to ``smartctl`` would
block, and why the skill instructs an agent to look figures up itself rather than
expect the tool to fetch them.

That one exception is confined to ``adapters/uv_bootstrap.py``: it alone imports
``subprocess`` (to run the official uv installer, which makes the network request
itself), and the second group of tests below holds the confinement both ways -
no other module gains it, and that module keeps it, so the allow-list cannot
quietly widen and cannot quietly become vacuous.

The one process lsdsk ever starts besides the installer is itself: the interactive view relaunches
elevated through pwshpy's ``ps.elevate`` - a UAC prompt on Windows (the user's
decision of 2026-10-16), ``sudo`` on Linux and macOS (2026-10-17), which
pwshpy starts. pwshpy is reached by NAME through importlib, so the import graph
below cannot see it, and the second test pins it to the one module allowed to
start that process.

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


#: The one module allowed to shell out, so it can run the official uv installer
#: when the reader consents. Named individually, like the forbidden roots, so
#: adding a second allowed module is a deliberate act with a test attached.
BOOTSTRAP = "adapters/uv_bootstrap.py"


def _shells_out_or_opens_a_socket() -> dict[str, list[str]]:
    """Every module importing a forbidden root, and which roots, by relative path."""
    return {
        str(module.relative_to(SRC)).replace("\\", "/"): sorted(found)
        for module in _modules()
        if (found := _imported_roots(module) & FORBIDDEN_ROOTS)
    }


@pytest.mark.os_agnostic
def test_no_module_shells_out_or_opens_a_socket() -> None:
    """The claim itself, over every module including the deferred imports.

    The one consent-gated installer module is allow-listed; every other module
    is held to the original claim.
    """
    offenders = {path: roots for path, roots in _shells_out_or_opens_a_socket().items() if path != BOOTSTRAP}
    assert not offenders, (
        "lsdsk documents that it issues no subprocesses and makes no network requests "
        "outside the consent-gated uv installer, "
        f"but these modules import otherwise: {offenders}"
    )


@pytest.mark.os_agnostic
def test_only_the_uv_bootstrap_module_shells_out_and_it_still_does() -> None:
    """The allow-list is confined to the one module AND is not vacuous.

    One direction stops a second module quietly gaining a subprocess under the
    exception; the other stops the exception outliving the code it was granted
    for - if ``uv_bootstrap`` ever stops shelling out, the exception should go,
    not sit there widening the posture for nothing.
    """
    reaching = _shells_out_or_opens_a_socket()
    assert set(reaching) == {BOOTSTRAP}, f"subprocess/network is reached outside {BOOTSTRAP}: {sorted(reaching)}"
    assert reaching[BOOTSTRAP] == ["subprocess"], (
        f"{BOOTSTRAP} is expected to reach only subprocess (the installer makes the network request itself), "
        f"but it imports: {reaching[BOOTSTRAP]}"
    )


#: The one module allowed to reach pwshpy, whose ``ps.elevate`` starts the
#: elevated copy - the only process lsdsk ever starts through the library.
RELAUNCHES = "adapters/hw/elevation.py"


def _reaches_pwshpy(source: str) -> bool:
    """Whether the module IMPORTS pwshpy, statically or through ``import_module``.

    Keyed on the import, not on the text: elevation.py reaches pwshpy by name
    through ``importlib.import_module("pwshpy")``, which the plain import graph
    cannot see, so that dynamic form is matched here too. A bare ``"pwshpy"``
    string - a package name in a cooldown-exemption list, say - is not a reach
    and must not be read as one, which a raw text match could not tell apart.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "pwshpy" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "pwshpy":
            return True
        if isinstance(node, ast.Call) and _is_dynamic_import_of(node, "pwshpy"):
            return True
    return False


def _is_dynamic_import_of(call: ast.Call, package: str) -> bool:
    """Whether ``call`` is ``import_module("<package>")`` or ``__import__("<package>")``."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    if name not in {"import_module", "__import__"} or not call.args:
        return False
    first = call.args[0]
    return isinstance(first, ast.Constant) and first.value == package


@pytest.mark.os_agnostic
def test_the_pwshpy_scan_sees_a_reach_but_not_a_bare_package_name() -> None:
    """The negative control, and the false-positive control beside it.

    The first arms make a clean tree mean something; the last two keep the guard
    from firing on a package name that only appears as a string, which is what a
    raw text match did when the owned-closure exemption list began naming pwshpy.
    """
    assert _reaches_pwshpy("import pwshpy\n")
    assert _reaches_pwshpy("from pwshpy import ps\n")
    assert _reaches_pwshpy('import importlib\nimportlib.import_module("pwshpy")\n')
    assert not _reaches_pwshpy('OWNED = ("lsdsk", "pwshpy", "fake-winreg")\n')
    assert not _reaches_pwshpy('"""The relaunch runs through pwshpy."""\n')


@pytest.mark.os_agnostic
def test_only_the_elevation_module_reaches_the_library_that_starts_a_process() -> None:
    """A second module importing pwshpy would be a second way to start a process."""
    naming = sorted(
        str(module.relative_to(SRC)).replace("\\", "/")
        for module in _modules()
        if _reaches_pwshpy(module.read_text(encoding="utf-8"))
    )
    assert naming == [RELAUNCHES], f"pwshpy is imported outside {RELAUNCHES}: {naming}"


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
