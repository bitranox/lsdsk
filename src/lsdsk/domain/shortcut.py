"""The desktop-shortcut feature's pure decisions, shared by every platform.

Everything here reads nothing and starts nothing: the facts are passed in, so
each rule is testable without a terminal, a config file, a filesystem or a
subprocess behind it. It covers whether to offer at all, the command the
shortcut runs, which launcher to target, and the consent text shown when uv must
be installed first; the adapters that read PATH, write files or shell out take
these answers and act on them.
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: The cooldown the desktop launcher pins, the same figure as lsdsk's own
#: ``[tool.uv].exclude-newer``. uv holds back every release younger than this
#: across the whole dependency tree, which is the supply-chain protection the
#: shortcut's consent text describes.
UVX_COOLDOWN: Final = "7 days"

#: lsdsk plus every package lsdsk owns in its dependency closure. A ``uvx`` run
#: started from the user's Desktop does not read lsdsk's published ``[tool.uv]``
#: (uv discovers config from the cwd and the user config, not from the installed
#: package), so the cooldown AND the exemptions that keep these uncooled must go
#: on the command line. Held equal to pyproject's ``exclude-newer-package = false``
#: entries by ``test_the_uvx_launch_command`` so the two cannot drift.
OWNED_CLOSURE: Final = (
    "lsdsk",
    "lib-cli-exit-tools",
    "lib-layered-config",
    "lib-log-rich",
    "pwshpy",
    "fake-winreg",
    "lib-registry",
)

#: The one-line description every platform's shortcut carries: the Linux
#: entry's ``Comment`` and the tooltip Explorer shows for the Windows ``.lnk``.
SHORTCUT_COMMENT: Final = "See your disks and controllers, and what is wrong with how they are connected"

#: The uv release lsdsk's installer is pinned to, so a reader runs a known
#: installer rather than whatever is newest. Bump it deliberately as uv releases,
#: the way a dependency floor is bumped; both the automatic install and the
#: manual line in the consent text are built from it, so they cannot diverge.
UV_INSTALLER_VERSION: Final = "0.13.0"

#: The official astral installer URLs for the pinned release, one per platform.
UV_INSTALLER_URL_POSIX: Final = f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.sh"
UV_INSTALLER_URL_WINDOWS: Final = f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.ps1"


class LaunchPlan(NamedTuple):
    """The command a shortcut should run, or the signal that uv must come first.

    Attributes:
        argv: The whole command, empty when ``needs_uv`` is set.
        needs_uv: Whether no launcher was found and uv must be installed before a
            shortcut can be made.
    """

    argv: tuple[str, ...]
    needs_uv: bool


class ShortcutWritten(NamedTuple):
    """The shortcut a platform wrote, and each copy of it that it could not write.

    Returned rather than warned about from inside the adapter, so the caller
    that reports the run - the JSON envelope included - holds every missing copy
    and cannot report ``ok`` over one by leaving it out.

    Attributes:
        path: The Desktop shortcut, which is the shortcut itself.
        skipped: One sentence per extra copy (an application-menu or Start-menu
            entry) that was not written, naming where and why; empty when none.
    """

    path: Path
    skipped: tuple[str, ...]


def plan_launch(*, uvx: Path | None, installed_lsdsk: Path | None) -> LaunchPlan:
    """Decide what a shortcut should launch, given what is on the machine.

    ``uvx`` is preferred because it carries the cooldown; a plainly installed
    lsdsk already on PATH is the fallback for a reader who installed it another
    way; with neither, uv has to be installed before there is anything to run.

    Args:
        uvx: The resolved ``uvx`` path, or ``None``.
        installed_lsdsk: A resolved plain ``lsdsk`` launcher on PATH, or ``None``.

    Returns:
        The launch plan.

    Example:
        >>> plan_launch(uvx=None, installed_lsdsk=None).needs_uv
        True
    """
    if uvx is not None:
        return LaunchPlan(uvx_launch_args(str(uvx)), needs_uv=False)
    if installed_lsdsk is not None:
        return LaunchPlan((str(installed_lsdsk),), needs_uv=False)
    return LaunchPlan((), needs_uv=True)


def manual_install_line(platform: str) -> str:
    """The one install command this platform's reader can run by hand.

    Args:
        platform: ``sys.platform`` of the machine the line is for.

    Returns:
        The command, built from the pinned installer URL.

    Example:
        >>> manual_install_line("linux").startswith("curl -LsSf https://astral.sh/uv/")
        True
    """
    if platform == "win32":
        return f'powershell -ExecutionPolicy ByPass -c "irm {UV_INSTALLER_URL_WINDOWS} | iex"'
    return f"curl -LsSf {UV_INSTALLER_URL_POSIX} | sh"


#: A run of backslashes before a double quote: the C runtime halves such a run, so it is
#: doubled and the quote escaped. Anywhere else a backslash is literal and passes unchanged.
_BACKSLASHES_BEFORE_A_QUOTE: Final = re.compile(r'(\\*)"')

#: Backslashes ending an argument that gets quoted: doubled, or they would escape the closing quote.
_TRAILING_BACKSLASHES: Final = re.compile(r"(\\*)\Z")


def _windows_argument(argument: str) -> str:
    """One argument quoted as the MS C runtime and ``CommandLineToArgvW`` read it back."""
    # Lambdas, not template strings: re would read the replacement's backslashes as escapes.
    escaped = _BACKSLASHES_BEFORE_A_QUOTE.sub(lambda run: run.group(1) * 2 + '\\"', argument)
    if argument and " " not in argument and "\t" not in argument:
        return escaped
    return '"' + _TRAILING_BACKSLASHES.sub(lambda run: run.group(1) * 2, escaped) + '"'


def command_line(argv: Sequence[str], *, platform: str) -> str:
    """The launch as one line a reader of ``platform`` can recognise or paste.

    Windows reads a command line by the C runtime's rules (CreateProcess, cmd),
    which know no single quotes, so the shell form printed a line that does not
    run there. The rules are those of ``subprocess.list2cmdline``, written out
    because this module starts nothing. PowerShell does NOT read this form as a
    command when its program is quoted: a quoted string followed by arguments is
    a parse error there, which is what :func:`powershell_line` is for. Every
    other platform gets the shell form.

    Args:
        argv: The command, executable first.
        platform: ``sys.platform`` of the machine the line is for.

    Returns:
        The whole command as one line.

    Example:
        >>> command_line(("C:/Program Files/uv/uvx.exe", "7 days"), platform="win32")
        '"C:/Program Files/uv/uvx.exe" "7 days"'
        >>> command_line(("/opt/my apps/lsdsk",), platform="linux")
        "'/opt/my apps/lsdsk'"
    """
    if platform == "win32":
        return " ".join(_windows_argument(argument) for argument in argv)
    return shlex.join(argv)


#: The typographic single quotes, which PowerShell reads as a single quote and escapes by doubling.
_POWERSHELL_SINGLE_QUOTES: Final = frozenset(("'", *map(chr, (0x2018, 0x2019, 0x201A, 0x201B))))

#: Characters PowerShell reads as syntax anywhere in a word, or inside the double
#: quotes of the cmd form (``$`` and the backtick expand there, and the
#: typographic double quotes are quotes): an argument holding one is single-quoted.
_POWERSHELL_SYNTAX: Final = (
    frozenset('$`"(){};,&|<>\r\n') | frozenset(map(chr, (0x201C, 0x201D, 0x201E))) | (_POWERSHELL_SINGLE_QUOTES)
)


def _reads_the_same_in_powershell(argument: str) -> bool:
    """Whether PowerShell reads the cmd form of ``argument`` as ``argument`` itself.

    PowerShell takes a double-quoted word literally apart from ``$`` and the
    backtick, so the C runtime's escapes do not apply there: a quoted argument
    ending in a backslash, whose backslashes the cmd form doubles, would arrive
    with the doubled run. A word starting ``@`` (splatting) or ``#`` (a comment)
    and the stop-parsing token ``--%`` mean something of their own.
    """
    if not argument or argument == "--%" or argument.startswith(("@", "#")):
        return False
    if not _POWERSHELL_SYNTAX.isdisjoint(argument):
        return False
    quoted = " " in argument or "\t" in argument
    return not (quoted and argument.endswith("\\"))


def _powershell_argument(argument: str) -> str:
    """One argument as PowerShell reads it: its cmd form where that is the same, else a literal string."""
    if _reads_the_same_in_powershell(argument):
        return _windows_argument(argument)
    literal = "".join(character * 2 if character in _POWERSHELL_SINGLE_QUOTES else character for character in argument)
    return f"'{literal}'"


def powershell_line(argv: Sequence[str]) -> str:
    """The launch as a line PowerShell runs: the call operator, then each argument as PowerShell reads it.

    A line beginning with a quoted program is an expression to PowerShell, and
    the arguments after it a parse error, so the program is invoked with ``&``.
    Each argument keeps its cmd form (:func:`command_line`) wherever PowerShell
    reads that form as the argument itself, which is every launch lsdsk builds
    from an ordinary path, so this is then the cmd line with ``& `` in front. An
    argument PowerShell would read as syntax - a ``$`` it would expand, a quote,
    a parenthesis, a semicolon - is written as a single-quoted literal instead.

    Args:
        argv: The command, executable first.

    Returns:
        The whole command as one PowerShell line.

    Example:
        >>> powershell_line(("C:/Program Files/uv/uvx.exe", "7 days"))
        '& "C:/Program Files/uv/uvx.exe" "7 days"'
        >>> powershell_line(("C:/Users/O'Brien/uvx.exe", "$HOME"))
        "& 'C:/Users/O''Brien/uvx.exe' '$HOME'"
    """
    return "& " + " ".join(_powershell_argument(argument) for argument in argv)


def consent_text(platform: str) -> str:
    """The explanation shown once, when the shortcut is accepted and uv is missing.

    It carries the supply-chain rationale (the cooldown) and the single manual
    install command for THIS platform, so a reader who would rather install uv
    themselves already has the line. The yes/no question is asked separately, so
    it is not part of this text.

    Args:
        platform: ``sys.platform`` of the running machine.

    Returns:
        The consent explanation, ending with the manual install command.
    """
    return (
        "lsdsk runs through uv, which pins a 7-day cooldown on new releases. uv holds back any\n"
        "release younger than the cooldown across the whole dependency tree, the transitive\n"
        "dependencies you never named included, so a package compromised today cannot reach your\n"
        "machine for 7 days. That is the supply-chain protection behind this shortcut.\n"
        "\n"
        "uv is not installed yet. lsdsk can install it for you now from the official installer at\n"
        "astral.sh, or you can install it yourself first:\n"
        "\n"
        f"  {manual_install_line(platform)}"
    )


def uvx_launch_args(uvx: str) -> tuple[str, ...]:
    """The argv a desktop shortcut runs to start lsdsk through ``uvx``.

    The resolved ``uvx`` executable leads, the cooldown and the owned-closure
    exemptions follow, and ``lsdsk@latest`` closes it. Pure: the caller resolves
    ``uvx`` to an absolute path and the platform adapter splits this into a
    target and its arguments however that platform's shortcut needs.

    Args:
        uvx: The absolute path to the ``uvx`` executable.

    Returns:
        The whole command, executable first.

    Example:
        >>> args = uvx_launch_args("/usr/bin/uvx")
        >>> args[0], args[1], args[2], args[-1]
        ('/usr/bin/uvx', '--exclude-newer', '7 days', 'lsdsk@latest')
    """
    args = [uvx, "--exclude-newer", UVX_COOLDOWN]
    for package in OWNED_CLOSURE:
        args += ["--exclude-newer-package", f"{package}=false"]
    args.append("lsdsk@latest")
    return tuple(args)


def should_offer(
    *,
    at_terminal: bool,
    offer_enabled: bool,
    desktop_present: bool,
    shortcut_present: bool,
) -> bool:
    """Decide whether to offer to create a desktop shortcut.

    The offer fires only when someone can answer it, the configuration still
    permits it, the account has a desktop to put a shortcut on, and it has not
    already been done. Any one of those being false is reason enough to stay
    silent. ``desktop_present`` has no default on purpose: a headless server is
    exactly the case where every other fact says yes.

    Args:
        at_terminal: Whether an interactive terminal is attached to answer.
        offer_enabled: Whether ``display.offer_desktop_shortcut`` still permits it.
        desktop_present: Whether the account has a desktop folder at all.
        shortcut_present: Whether the platform's shortcut already exists.

    Returns:
        ``True`` when the offer should be shown, ``False`` otherwise.

    Example:
        >>> should_offer(at_terminal=True, offer_enabled=True, desktop_present=True, shortcut_present=False)
        True
        >>> should_offer(at_terminal=True, offer_enabled=True, desktop_present=True, shortcut_present=True)
        False
        >>> should_offer(at_terminal=True, offer_enabled=True, desktop_present=False, shortcut_present=False)
        False
    """
    return at_terminal and offer_enabled and desktop_present and not shortcut_present


__all__ = [
    "OWNED_CLOSURE",
    "SHORTCUT_COMMENT",
    "UVX_COOLDOWN",
    "UV_INSTALLER_URL_POSIX",
    "UV_INSTALLER_URL_WINDOWS",
    "UV_INSTALLER_VERSION",
    "LaunchPlan",
    "ShortcutWritten",
    "command_line",
    "consent_text",
    "manual_install_line",
    "plan_launch",
    "powershell_line",
    "should_offer",
    "uvx_launch_args",
]
