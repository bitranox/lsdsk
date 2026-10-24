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

An ``import`` line is not the only way in, so the scan follows the others too:
the process starters ``os`` and ``asyncio`` carry (``os.system``, ``os.exec*``,
``create_subprocess_exec``), whether called, bound under an alias or imported
by name; ``multiprocessing``, ``pty`` and a process pool; a module imported by
``import_module`` or ``__import__``, and refused outright when its name is
computed, since nothing can check it. ctypes reaches libc's ``system`` and
Win32's ``CreateProcessW`` as easily as an ioctl, so it is confined to the
platform transports that need it, which in turn may name no C routine that
starts a process. Every rule has a planted sample that must read as its label.
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
        # Whole modules whose purpose is a second process: a worker pool, and a
        # child on a pseudo-terminal.
        "multiprocessing",
        "pty",
    }
)

#: Process starters reached through a module that is otherwise fine to import,
#: as the module and the PREFIXES of the member names that start a process:
#: ``os.exec*``, ``os.spawn*``, ``os.posix_spawn*`` and ``os.fork*`` are families,
#: and ``*`` stands for a star import, which would bring every one of them in.
#: ``os.startfile`` is here too: on Windows it hands a file to the shell, which
#: starts whatever program is registered for it.
FORBIDDEN_MEMBERS: dict[str, tuple[str, ...]] = {
    "os": ("system", "popen", "startfile", "exec", "spawn", "posix_spawn", "fork", "*"),
    "asyncio": ("create_subprocess_", "subprocess", "*"),
    "concurrent.futures": ("ProcessPoolExecutor", "process", "*"),
}

#: What a dynamic import whose module name is not a literal reads as. It cannot
#: be checked against anything above, so it is refused wherever it appears.
COMPUTED_IMPORT = "import_module(<a name computed at run time>)"


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_roots(module: Path) -> set[str]:
    """Every forbidden way this module reaches a subprocess or a socket, by label.

    Includes imports nested inside functions, which is where a shell-out would
    most plausibly appear: the project defers several imports to keep the fast
    path flat, so a top-of-file-only scan would miss exactly the shape it is
    looking for. Labels are a forbidden root (``subprocess``) or a forbidden
    member (``os.system``); see :func:`_forbidden_reaches`.
    """
    return _forbidden_reaches(module.read_text(encoding="utf-8"))


def _forbidden_reaches(source: str) -> set[str]:
    """Label every reference in ``source`` that reaches a subprocess or a socket.

    A reference is any dotted name the code can reach: what an ``import`` or
    ``from ... import`` names, an attribute read off a name an import bound
    (``_os.execvp`` after ``import os as _os``, called or only bound), a
    ``getattr`` of a literal attribute off such a name, and the literal module
    name of ``import_module(...)`` or ``__import__(...)``. Relative imports are
    left out: they name this package's own modules, which are scanned as files.
    """
    found: set[str] = set()
    for reference in _references(ast.parse(source)):
        label = _forbidden_label(reference)
        if label is not None:
            found.add(label)
    return found


def _forbidden_label(reference: str) -> str | None:
    """The label a reference is refused under, or None when it reaches nothing forbidden."""
    if reference == COMPUTED_IMPORT:
        return reference
    parts = reference.split(".")
    if parts[0] in FORBIDDEN_ROOTS:
        return parts[0]
    for split in range(1, len(parts)):
        module, member = ".".join(parts[:split]), parts[split]
        if member.startswith(FORBIDDEN_MEMBERS.get(module, ())):
            return f"{module}.{member}"
    return None


def _references(tree: ast.AST) -> list[str]:
    """Every dotted name ``tree`` imports or reads off an imported name, in no particular order."""
    bound: dict[str, str] = {}
    references: list[str] = []
    for node in ast.walk(tree):
        references.extend(_import_references(node, bound))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            references.extend(_resolved(node, bound))
        elif isinstance(node, ast.Call):
            references.extend(_call_references(node, bound))
    return references


def _import_references(node: ast.AST, bound: dict[str, str]) -> list[str]:
    """What an import statement names, recording the local name each one binds."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        named = [f"{node.module}.{alias.name}" for alias in node.names]
        for alias, full in zip(node.names, named, strict=True):
            bound[alias.asname or alias.name] = full
        return [node.module, *named]
    return []


def _resolved(node: ast.Attribute, bound: dict[str, str]) -> list[str]:
    """``a.b.c`` with ``a`` replaced by what an import bound it to; nothing when ``a`` is not an import."""
    attributes: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        attributes.insert(0, current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id not in bound:
        return []
    return [".".join([bound[current.id], *attributes])]


def _call_references(call: ast.Call, bound: dict[str, str]) -> list[str]:
    """A dynamic import's module, or a ``getattr`` of a literal name off an imported name."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    first = call.args[0] if call.args else None
    if name in {"import_module", "__import__"} and first is not None:
        literal = _literal_text(first)
        return [literal] if literal is not None else [COMPUTED_IMPORT]
    if name == "getattr" and len(call.args) >= 2 and isinstance(first, ast.Name) and first.id in bound:
        member = _literal_text(call.args[1])
        if member is not None:
            return [f"{bound[first.id]}.{member}"]
    return []


def _literal_text(node: ast.expr) -> str | None:
    """The text of a string literal, or None for anything computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


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


#: Each way a module could start a process or open a socket that the import
#: graph alone does not show, planted in a sample and the label it must read as.
PLANTED_REACHES = [
    pytest.param("import os\nos.system('true')\n", {"os.system"}, id="os.system called"),
    pytest.param("import os as _os\n_RUN = _os.system\n", {"os.system"}, id="os.system bound under an alias"),
    pytest.param("from os import popen as _popen\n_POPEN = _popen\n", {"os.popen"}, id="from os import popen"),
    pytest.param("import os as _os\n_EXEC = _os.execvp\n", {"os.execvp"}, id="os.execvp bound"),
    pytest.param("from os import execvp\n", {"os.execvp"}, id="from os import execvp"),
    pytest.param("import os\nos.spawnlp(0, 'x', 'x')\n", {"os.spawnlp"}, id="os.spawn family"),
    pytest.param("import os\nos.posix_spawnp('x', ['x'], {})\n", {"os.posix_spawnp"}, id="os.posix_spawnp"),
    pytest.param("import os\nos.forkpty()\n", {"os.forkpty"}, id="os.fork family"),
    pytest.param("import os\nos.startfile('x')\n", {"os.startfile"}, id="os.startfile"),
    pytest.param("from os import *\n", {"os.*"}, id="a star import from os"),
    pytest.param("import os\nrun = getattr(os, 'system')\n", {"os.system"}, id="getattr of os.system"),
    pytest.param(
        "import asyncio\nasyncio.create_subprocess_exec('x')\n",
        {"asyncio.create_subprocess_exec"},
        id="asyncio.create_subprocess_exec",
    ),
    pytest.param(
        "from asyncio import create_subprocess_shell\n",
        {"asyncio.create_subprocess_shell"},
        id="from asyncio import create_subprocess_shell",
    ),
    pytest.param("import asyncio.subprocess\n", {"asyncio.subprocess"}, id="asyncio.subprocess"),
    pytest.param("import multiprocessing as _mp\n_PROC = _mp.Process\n", {"multiprocessing"}, id="multiprocessing"),
    pytest.param("import pty\npty.spawn('x')\n", {"pty"}, id="pty"),
    pytest.param(
        "from concurrent.futures import ProcessPoolExecutor\n",
        {"concurrent.futures.ProcessPoolExecutor"},
        id="a process pool",
    ),
    pytest.param(
        "import concurrent.futures.process\n", {"concurrent.futures.process"}, id="concurrent.futures.process"
    ),
    pytest.param(
        "import importlib as _il\n_SP = _il.import_module('subprocess')\n",
        {"subprocess"},
        id="import_module subprocess",
    ),
    pytest.param(
        "from importlib import import_module\nimport_module('socket')\n", {"socket"}, id="a bare import_module"
    ),
    pytest.param("_s = __import__('socket')\n", {"socket"}, id="__import__ socket"),
    pytest.param(
        "import importlib\nimportlib.import_module('http.client')\n", {"http"}, id="import_module http.client"
    ),
    pytest.param(
        "import importlib as _il\n_p = _il.import_module('pw' + 'shpy')\n",
        {COMPUTED_IMPORT},
        id="a module named at run time",
    ),
]


@pytest.mark.parametrize(("source", "labels"), PLANTED_REACHES)
def test_the_scan_sees_each_way_to_reach_a_process_or_a_socket(source: str, labels: set[str]) -> None:
    """The negative control for every rule, run through the scanner the tree checks use.

    Each sample is one way a module could start a process or open a socket
    without an ``import subprocess`` line; each must read as exactly its label,
    or a clean tree would mean only that the scanner cannot see that way.
    """
    assert _forbidden_reaches(source) == labels


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import os\nos.path.join('a', 'b')\nos.environ.get('X')\nos.getpid()\n", id="os itself"),
        pytest.param("import asyncio\nasyncio.run(asyncio.sleep(0))\n", id="an event loop"),
        pytest.param("from concurrent.futures import ThreadPoolExecutor\n", id="a thread pool"),
        pytest.param("import platform\nplatform.system()\n", id="platform.system names no process"),
        pytest.param('"""This never calls os.system or opens a socket."""\n', id="a docstring saying so"),
        pytest.param("OWNED = ('subprocess', 'socket', 'pwshpy')\n", id="package names as data"),
        pytest.param("from .subprocess import helper\n", id="a relative import names this package"),
        pytest.param("import importlib\nimportlib.import_module('winreg')\n", id="a literal harmless module"),
    ],
)
def test_the_scan_does_not_read_harmless_code_as_a_reach(source: str) -> None:
    """The false-positive control beside it: code the tree is full of must stay clean."""
    assert _forbidden_reaches(source) == set()


#: The one module allowed to shell out, so it can run the official uv installer
#: when the reader consents. Named individually, like the forbidden roots, so
#: adding a second allowed module is a deliberate act with a test attached.
BOOTSTRAP = "adapters/uv_bootstrap.py"


def _shells_out_or_opens_a_socket() -> dict[str, list[str]]:
    """Every module importing a forbidden root, and which roots, by relative path."""
    return {
        str(module.relative_to(SRC)).replace("\\", "/"): sorted(found)
        for module in _modules()
        if (found := _imported_roots(module))
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


#: The modules that reach C through ctypes: the platform transports, which issue
#: ioctls and call Win32 directly. Named individually, like the other allow-lists,
#: so a new caller of C is a deliberate act with a test attached - ctypes can
#: reach libc's ``system`` or Win32's ``CreateProcessW`` as easily as an ioctl.
CTYPES_USERS = frozenset(
    {
        "adapters/hw/linux/reader.py",
        "adapters/hw/linux/usbfs.py",
        "adapters/hw/linux/zfs.py",
        "adapters/hw/windows/reader.py",
        "adapters/hw/windows/volumes.py",
        "adapters/hw/windows/winapi.py",
    }
)

#: The C entry points that start a process, as a ctypes caller would spell them.
FOREIGN_PROCESS_STARTERS = frozenset(
    {
        "system",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "vfork",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "CreateProcessA",
        "CreateProcessW",
        "CreateProcessAsUserW",
        "CreateProcessWithLogonW",
        "ShellExecuteA",
        "ShellExecuteW",
        "ShellExecuteExW",
        "WinExec",
    }
)


def _reaches_ctypes(source: str) -> bool:
    """Whether ``source`` imports ctypes, statically or through a dynamic import."""
    return any(reference.split(".")[0] == "ctypes" for reference in _references(ast.parse(source)))


def _foreign_process_starters(source: str) -> set[str]:
    """Every C process starter ``source`` names as an attribute or as a literal outside a docstring."""
    tree = ast.parse(source)
    skip = _docstring_ids(tree)
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FOREIGN_PROCESS_STARTERS:
            named.add(node.attr)
        elif isinstance(node, ast.Constant) and node.value in FOREIGN_PROCESS_STARTERS and id(node) not in skip:
            named.add(str(node.value))
    return named


def test_the_ctypes_scans_see_a_reach_and_a_foreign_process_starter() -> None:
    """The negative controls for the two ctypes rules, and the false-positive control beside them."""
    assert _reaches_ctypes("import ctypes\n")
    assert _reaches_ctypes("from ctypes import wintypes\n")
    assert _reaches_ctypes("import importlib\nimportlib.import_module('ctypes')\n")
    assert not _reaches_ctypes('"""Reads registers through ctypes."""\n')
    assert _foreign_process_starters("libc = ctypes.CDLL(None)\nlibc.system(b'true')\n") == {"system"}
    assert _foreign_process_starters("run = getattr(libc, 'execvp')\n") == {"execvp"}
    assert _foreign_process_starters("kernel32.CreateProcessW(None)\n") == {"CreateProcessW"}
    assert _foreign_process_starters('def read():\n    """Never calls system()."""\n') == set()
    assert _foreign_process_starters("kernel32.DeviceIoControl(handle)\n") == set()


def test_only_the_platform_transports_reach_ctypes_and_each_still_does() -> None:
    """A second module calling C would be a second way to start a process; a listed one that stopped is stale."""
    reaching = {
        str(module.relative_to(SRC)).replace("\\", "/")
        for module in _modules()
        if _reaches_ctypes(module.read_text(encoding="utf-8"))
    }
    assert reaching == CTYPES_USERS, (
        f"ctypes is reached outside the transports: {sorted(reaching - CTYPES_USERS)}; "
        f"listed but no longer reaching it: {sorted(CTYPES_USERS - reaching)}"
    )


def test_no_caller_of_c_names_a_process_starter() -> None:
    """The transports may call C, and what they call is never a C routine that starts a process."""
    offenders = {
        path: sorted(found)
        for path in sorted(CTYPES_USERS)
        if (found := _foreign_process_starters((SRC / path).read_text(encoding="utf-8")))
    }
    assert not offenders, f"a ctypes caller names a C routine that starts a process: {offenders}"
