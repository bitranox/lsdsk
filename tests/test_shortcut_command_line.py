"""How the install-shortcut report spells the launch: the CreateProcess form on Windows, the shell form elsewhere.

``subprocess.list2cmdline`` is the oracle for the Windows form: lsdsk cannot import
``subprocess`` outside its uv installer, so it carries its own quoter, and these
hold the two to one answer. On a Windows cell the line is also handed to the
shell's own parser and must come back as the argv it was built from. One
deliberate difference: an argument holding one of cmd's operators is quoted,
which CreateProcess reads the same and which keeps cmd from acting on it, so the
line pasted into cmd runs too - measured on Windows, where the unquoted form
split ``a&b`` into a second command and lost the caret of ``a^b``.
"""

from __future__ import annotations

import ctypes
import json
import os
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.uv_bootstrap import run_installer
from lsdsk.domain.shortcut import command_line, uvx_launch_args

if TYPE_CHECKING:
    from pathlib import Path

_ARGVS = [
    pytest.param(("C:\\Program Files\\uv\\uvx.exe", "--exclude-newer", "7 days", "lsdsk@latest"), id="spaces"),
    pytest.param(("C:\\tools\\lsdsk.exe",), id="no-quoting-needed"),
    pytest.param(("prog", ""), id="empty-argument"),
    pytest.param(("prog", 'say "hi"'), id="embedded-quote"),
    pytest.param(("prog", 'a\\"b'), id="backslash-before-quote"),
    pytest.param(("prog", 'with space\\"q'), id="backslash-before-quote-quoted"),
    pytest.param(("prog", "C:\\dir with space\\"), id="trailing-backslash-quoted"),
    pytest.param(("prog", "C:\\dir\\"), id="trailing-backslash-bare"),
    pytest.param(("prog", "tab\there"), id="tab"),
    pytest.param(("prog", "C:\\dir with space\\\\"), id="double-trailing-backslash-quoted"),
    pytest.param(("prog", "with space\\\n"), id="backslash-newline-quoted"),
]


@pytest.mark.os_agnostic
def test_the_windows_form_of_a_space_bearing_uvx_launch_is_the_createprocess_form() -> None:
    launch = uvx_launch_args("C:\\Users\\Jane Doe\\.local\\bin\\uvx.exe")

    line = command_line(launch, platform="win32")

    assert line.startswith('"C:\\Users\\Jane Doe\\.local\\bin\\uvx.exe" --exclude-newer "7 days" ')
    assert line.endswith(" lsdsk@latest")
    assert "'" not in line, "cmd and PowerShell do not read POSIX single quotes"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("argv", _ARGVS)
def test_the_windows_form_agrees_with_list2cmdline(argv: tuple[str, ...]) -> None:
    assert command_line(argv, platform="win32") == subprocess.list2cmdline(argv)


#: Arguments holding one of cmd's operators, each of which cmd acts on outside double quotes.
_CMD_OPERATORS = [
    pytest.param(("prog", "a&b"), id="ampersand"),
    pytest.param(("prog", "a|b"), id="pipe"),
    pytest.param(("prog", "a<b"), id="less-than"),
    pytest.param(("prog", "a>b"), id="greater-than"),
    pytest.param(("prog", "a^b"), id="caret"),
    pytest.param(("C:\\Users\\R&D\\uvx.exe", "--exclude-newer", "7 days"), id="operator-in-the-program"),
]


def test_an_argument_holding_a_cmd_operator_is_double_quoted() -> None:
    """Outside quotes cmd ends the command at ``&`` and ``|``, redirects at ``<``/``>`` and drops a caret."""
    assert command_line(("prog", "a&b", "a^b", "plain"), platform="win32") == 'prog "a&b" "a^b" plain'
    assert command_line(("C:\\R&D\\uvx.exe",), platform="win32") == '"C:\\R&D\\uvx.exe"'


@pytest.mark.os_windows
@pytest.mark.parametrize("argv", [*_ARGVS, *_CMD_OPERATORS])
def test_windows_parses_the_windows_form_with_a_cmd_operator_back_into_the_same_argv(argv: tuple[str, ...]) -> None:
    """The quoting cmd needs is one CreateProcess reads the same: the argv comes back unchanged."""
    assert _parsed_by_windows(command_line(argv, platform="win32")) == argv


@pytest.mark.os_agnostic
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_every_other_platform_gets_the_shell_form(platform: str) -> None:
    argv = ("/opt/my apps/lsdsk", "--x", "7 days")

    assert command_line(argv, platform=platform) == shlex.join(argv)


@pytest.mark.os_windows
@pytest.mark.parametrize("argv", _ARGVS)
def test_windows_parses_the_windows_form_back_into_the_same_argv(argv: tuple[str, ...]) -> None:
    assert _parsed_by_windows(command_line(argv, platform="win32")) == argv


def _parsed_by_windows(line: str) -> tuple[str, ...]:
    """``line`` as ``CommandLineToArgvW`` reads it back."""
    assert sys.platform == "win32"  # narrows ctypes.windll for pyright
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    count = ctypes.c_int()
    # A leading dummy program: Windows parses the first token by simpler rules than the rest.
    parsed = shell32.CommandLineToArgvW("x " + line, ctypes.byref(count))
    try:
        return tuple(parsed[i] for i in range(1, count.value))
    finally:
        ctypes.windll.kernel32.LocalFree(parsed)


#: Each argument's received form is appended to the file the first argument names, one JSON line per run.
_RECORD_ARGV = "import sys,json;open(sys.argv[1],'a',encoding='utf-8').write(json.dumps(sys.argv[2:])+chr(10))"


#: What a reader can paste into cmd: a newline ends the line there, and no Windows path holds one.
_ONE_LINE = [param for param in _ARGVS if param.id != "backslash-newline-quoted"]


@pytest.mark.os_windows
@pytest.mark.parametrize("argv", [*_ONE_LINE, *_CMD_OPERATORS])
def test_cmd_runs_the_windows_form_with_the_argv_it_was_built_from(argv: tuple[str, ...], tmp_path: Path) -> None:
    """The line pasted into cmd reaches the program as the argv it was built from.

    Run, not parsed: cmd acts on its operators before any program sees the
    line, and only the program's own argv shows what survived. The line is run
    from a batch file, so cmd gets it byte for byte and reads its operators and
    quotes by the rules of its prompt. A ``%NAME%`` naming a set variable is
    expanded inside double quotes too, at the prompt and in a batch file alike,
    so no argument here holds a percent sign: the docstring of ``command_line``
    says so instead.
    """
    record = tmp_path / "argv.jsonl"
    launch = (sys.executable, "-c", _RECORD_ARGV, str(record), *argv[1:])
    pasted = tmp_path / "pasted.cmd"
    pasted.write_bytes(("@" + command_line(launch, platform="win32") + "\r\n").encode("utf-8"))

    # Through lsdsk's own argv runner, the one seam in the package that runs a command; cmd by its absolute path.
    assert run_installer([os.environ["COMSPEC"], "/d", "/c", str(pasted)]) == 0
    assert json.loads(record.read_text(encoding="utf-8")) == list(argv[1:])
