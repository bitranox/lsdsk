"""The PowerShell form of the install-shortcut launch line, held against PowerShell's own parser.

``command_line`` prints the launch the way CreateProcess and cmd read it, and a
line that begins with a quoted program is not a command PowerShell will run:
``"C:/Program Files/uv/uvx.exe" --exclude-newer`` is a parse error there, because
a quoted string is an expression and the next word is an unexpected token. The
call operator ``&`` makes it a command, so on Windows the report adds a second
line carrying it.

Where ``pwsh`` is installed the line is handed to PowerShell's own parser, which
must find exactly one command, invoked with ``&``, whose words are literal
strings equal to the argv the line was built from - and then RUN, because the
parser is not the last word: PowerShell splits ``-a.b`` into two arguments,
expands a leading ``~`` and drops ``--%`` when it hands a native program its
argv, and a word the parser reads as one string constant arrives as two.
"""

from __future__ import annotations

import json
import shutil
import sys
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
    ("prog", "a`b", "@splat", "#comment", ""),
    # The stop-parsing token's neighbours, which do reach the program.
    ("prog", "--%x", "a--%", "-", "--"),
    ("prog", "C:\\dir with space\\", "C:\\dir with space\\\\", 'say "hi"', "tab\there", "two\nlines"),
    # PowerShell reads the typographic quotes as quotes too.
    ("prog", "O" + chr(0x2019) + "Brien", "a" + chr(0x201C) + "b" + chr(0x201D)),
    # Each quote alone, so dropping any one of them from the set is seen.
    ("prog", *("a" + chr(quote) + "b" for quote in (0x2018, 0x2019, 0x201A, 0x201B, 0x201C, 0x201D, 0x201E))),
    # A carriage return alone, and a tab with no space beside a trailing backslash.
    ("prog", "a\rb", "tab\there\\"),
    # A leading closing bracket: pwsh 7.6 splits "]b" into "]" and "b" and refuses "]@" as a parse error.
    ("prog", "]b", "]@", "]]", "]x.y"),
]

#: Words PowerShell's parser reads as one string and the native call then changes, measured on pwsh 7.6
#: on Linux and Windows and on Windows PowerShell 5.1: whitespace that is not ASCII splits the word, a
#: dash word holding a dot is split at it, a dash word ending in a colon is a parse error as the last
#: word, and a leading tilde is replaced by the home folder (pwsh only).
_CHANGED_BY_THE_CALL: list[tuple[str, ...]] = [
    ("prog", *("a" + chr(space) + "b" for space in (0x00A0, 0x2003, 0x3000, 0x2028))),
    ("prog", "-a.b", "--x.y", "-1.5", "-a=b"),
    ("prog", "-x:"),
    ("prog", "~", "~/x", "~\\x", "x~y"),
    ("prog", "J" + chr(0xFC) + "rgen"),
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
    lines = [powershell_line(argv) for argv in [*_ARGVS, *_CHANGED_BY_THE_CALL]]

    parsed = _parsed_by_pwsh(pwsh, lines, tmp_path)

    assert len(parsed) == len(lines)
    for argv, line, reading in zip([*_ARGVS, *_CHANGED_BY_THE_CALL], lines, parsed, strict=True):
        assert reading.errors == 0, line
        assert reading.commands == 1, line
        assert reading.operator == "Ampersand", line
        assert reading.redirections == 0, line
        assert set(reading.kinds) == {"StringConstantExpressionAst"}, line
        assert reading.values == list(argv), line


#: Run each line through ``Invoke-Expression``, so one line's parse error does not stop the others, and
#: mark where each ends; the program each line runs appends what it received before the mark.
_RUN = r"""
param([string]$In, [string]$Out)
$ErrorActionPreference = 'Stop'
$lines = Get-Content -LiteralPath $In -Raw -Encoding utf8 | ConvertFrom-Json
foreach ($line in $lines) {
    try { Invoke-Expression $line } catch { [System.IO.File]::AppendAllText($Out, "!error`n") }
    [System.IO.File]::AppendAllText($Out, "@@`n")
}
"""

#: The program each line runs: it appends the arguments after its first to the file its first names.
_RECORD_ARGV = "import sys,json;open(sys.argv[1],'a',encoding='utf-8').write(json.dumps(sys.argv[2:])+chr(10))"


def _run_by_pwsh(pwsh: str, lines: list[str], record: Path, tmp_path: Path) -> list[list[str] | None]:
    """What the program each line runs received, in order; ``None`` where the line did not run it."""
    script = tmp_path / "run.ps1"
    script.write_bytes(_RUN.encode("utf-8"))
    given = tmp_path / "run-lines.json"
    given.write_bytes(json.dumps(lines).encode("utf-8"))
    assert (
        run_installer(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script), "-In", str(given), "-Out", str(record)]
        )
        == 0
    )
    blocks = record.read_bytes().decode("utf-8").split("@@\n")[:-1]
    return [TypeAdapter(list[str]).validate_json(block) if block.startswith("[") else None for block in blocks]


def test_powershell_runs_each_line_with_the_argv_it_was_built_from(tmp_path: Path) -> None:
    """The program a line starts receives exactly the arguments the line was built from."""
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not installed, so no line can be run")
    record = tmp_path / "received.txt"
    cases = [argv[1:] for argv in [*_ARGVS, *_CHANGED_BY_THE_CALL]]
    lines = [powershell_line((sys.executable, "-c", _RECORD_ARGV, str(record), *case)) for case in cases]

    received = _run_by_pwsh(pwsh, lines, record, tmp_path)

    assert len(received) == len(cases)
    mismatched = [(list(case), got) for case, got in zip(cases, received, strict=True) if got != list(case)]
    assert not mismatched, mismatched


def test_the_stop_parsing_token_is_refused_because_no_spelling_of_it_reaches_the_program() -> None:
    """PowerShell drops an argument ``--%`` however it is written: bare, quoted either way, escaped, in parentheses.

    Measured on pwsh 7.6 and Windows PowerShell 5.1. A line that silently loses
    an argument is not the launch it claims to be, so none is written.
    """
    with pytest.raises(ValueError, match="--%"):
        powershell_line(("prog", "--%"))
    assert powershell_line(("prog", "--%x", "a--%")) == "& prog '--%x' a--%"
