"""How the install-shortcut report spells the launch: the CreateProcess form on Windows, the shell form elsewhere.

``subprocess.list2cmdline`` is the oracle for the Windows form: lsdsk cannot import
``subprocess`` outside its uv installer, so it carries its own quoter, and these
hold the two to one answer. On a Windows cell the line is also handed to the
shell's own parser and must come back as the argv it was built from.
"""

from __future__ import annotations

import ctypes
import shlex
import subprocess
import sys

import pytest

from lsdsk.domain.shortcut import command_line, uvx_launch_args

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


@pytest.mark.os_agnostic
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_every_other_platform_gets_the_shell_form(platform: str) -> None:
    argv = ("/opt/my apps/lsdsk", "--x", "7 days")

    assert command_line(argv, platform=platform) == shlex.join(argv)


@pytest.mark.os_windows
@pytest.mark.parametrize("argv", _ARGVS)
def test_windows_parses_the_windows_form_back_into_the_same_argv(argv: tuple[str, ...]) -> None:
    assert sys.platform == "win32"  # narrows ctypes.windll for pyright
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    count = ctypes.c_int()
    # A leading dummy program: Windows parses the first token by simpler rules than the rest.
    parsed = shell32.CommandLineToArgvW("x " + command_line(argv, platform="win32"), ctypes.byref(count))
    try:
        assert tuple(parsed[i] for i in range(1, count.value)) == argv
    finally:
        ctypes.windll.kernel32.LocalFree(parsed)
