"""The PowerShell form of the install-shortcut launch line, held against PowerShell's own parser.

``command_line`` prints the launch the way CreateProcess and cmd read it, and a
line that begins with a quoted program is not a command PowerShell will run:
``"C:/Program Files/uv/uvx.exe" --exclude-newer`` is a parse error there, because
a quoted string is an expression and the next word is an unexpected token. The
call operator ``&`` makes it a command, so on Windows the report adds a second
line carrying it.

Where ``pwsh`` is installed the line is handed to PowerShell's own parser, which
must find exactly one command, invoked with ``&``, whose words are literal
strings equal to the argv the line was built from.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, TypeAdapter

from lsdsk.adapters.uv_bootstrap import run_installer
from lsdsk.domain.shortcut import command_line, powershell_line, uvx_launch_args

if TYPE_CHECKING:
    from pathlib import Path

#: A launch through a uvx whose path holds a space, as a Windows profile path often does.
_REALISTIC = uvx_launch_args("C:\\Users\\Jane Doe\\.local\\bin\\uvx.exe")

_ARGVS: list[tuple[str, ...]] = [
    _REALISTIC,
    uvx_launch_args("C:\\Users\\jane\\.local\\bin\\uvx.exe"),
    ("C:\\Users\\O'Brien\\uvx.exe", "x"),
    ("C:\\Users\\Bob(x)\\uvx.exe", "x"),
    ("C:\\a b$c\\uvx.exe", "$HOME"),
    ("C:\\a{b}\\uvx.exe", "a,b", "a;b", "a&b", "a|b", "a<b", "a>b"),
    ("prog", "a`b", "@splat", "#comment", "--%", ""),
    ("prog", "C:\\dir with space\\", "C:\\dir with space\\\\", 'say "hi"', "tab\there", "two\nlines"),
    # PowerShell reads the typographic quotes as quotes too.
    ("prog", "O" + chr(0x2019) + "Brien", "a" + chr(0x201C) + "b" + chr(0x201D)),
]

_PARSE = r"""
param([string]$In, [string]$Out)
$lines = Get-Content -LiteralPath $In -Raw -Encoding utf8 | ConvertFrom-Json
$results = foreach ($line in $lines) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($line, [ref]$tokens, [ref]$errors)
    $commands = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true))
    $kinds = @(); $values = @(); $operator = ''; $redirections = 0
    if ($commands.Count -ge 1) {
        $operator = [string]$commands[0].InvocationOperator
        $redirections = $commands[0].Redirections.Count
        foreach ($element in $commands[0].CommandElements) {
            $kinds += $element.GetType().Name
            $values += [string]$element.Value
        }
    }
    [pscustomobject]@{ errors = @($errors).Count; commands = $commands.Count; operator = $operator;
                       redirections = $redirections; kinds = @($kinds); values = @($values) }
}
Set-Content -LiteralPath $Out -Encoding utf8 -Value (ConvertTo-Json -InputObject @($results) -Depth 5)
"""


def test_a_realistic_launch_is_the_cmd_form_behind_the_call_operator() -> None:
    """For every launch lsdsk prints in practice the PowerShell line is the cmd line with ``& `` in front."""
    for argv in (_REALISTIC, uvx_launch_args("C:\\Users\\jane\\.local\\bin\\uvx.exe")):
        assert powershell_line(argv) == "& " + command_line(argv, platform="win32")


def test_an_argument_powershell_reads_as_syntax_is_single_quoted_with_its_quotes_doubled() -> None:
    assert powershell_line(("C:\\Users\\O'Brien\\uvx.exe", "x")) == "& 'C:\\Users\\O''Brien\\uvx.exe' x"
    assert powershell_line(("prog", "$HOME")) == "& prog '$HOME'"


class _Reading(BaseModel):
    """What PowerShell's parser made of one line: its errors and its first command's words."""

    errors: int
    commands: int
    operator: str
    redirections: int
    kinds: list[str]
    values: list[str]


def _parsed_by_pwsh(pwsh: str, lines: list[str], tmp_path: Path) -> list[_Reading]:
    """Each line as PowerShell's parser reads it, through files so no console encoding touches the text.

    ``pwsh`` is started through lsdsk's own argv runner, the one seam in the
    package that runs a command and hands back its exit code.
    """
    script = tmp_path / "parse.ps1"
    script.write_bytes(_PARSE.encode("utf-8"))
    given = tmp_path / "lines.json"
    given.write_bytes(json.dumps(lines).encode("utf-8"))
    answer = tmp_path / "parsed.json"
    command = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script), "-In", str(given), "-Out", str(answer)]
    assert run_installer(command) == 0
    return TypeAdapter(list[_Reading]).validate_json(answer.read_bytes().decode("utf-8-sig"))


def test_powershell_parses_each_line_into_one_call_of_the_same_argv(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not installed, so PowerShell's own parser cannot be asked")
    lines = [powershell_line(argv) for argv in _ARGVS]

    parsed = _parsed_by_pwsh(pwsh, lines, tmp_path)

    assert len(parsed) == len(_ARGVS)
    for argv, line, reading in zip(_ARGVS, lines, parsed, strict=True):
        assert reading.errors == 0, line
        assert reading.commands == 1, line
        assert reading.operator == "Ampersand", line
        assert reading.redirections == 0, line
        assert set(reading.kinds) == {"StringConstantExpressionAst"}, line
        assert reading.values == list(argv), line
