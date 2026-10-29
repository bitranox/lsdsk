"""The ``install-shortcut`` command: create the desktop launcher deliberately.

A reader can ask for the shortcut outright rather than waiting for the offer the
interactive view makes. It is machine-drivable - ``--json`` and meaningful exit
codes, like ``config-deploy`` - so it can be scripted. It resolves a launcher
(``uvx`` first, then a plainly installed lsdsk on PATH), installs uv only when
asked with ``--install-uv``, and writes the platform's shortcut through the
:class:`~lsdsk.application.ports.DesktopShortcuts` port.

The thin Click command delegates to :func:`install_shortcut`, which takes its
collaborators as one injectable value, so a test drives the whole flow with a
recording shortcut double and controlled PATH lookups and never runs the real
installer - the same seam ``run_default_view`` uses for the interactive view.

System Role:
    Adapter-layer CLI command; one of the acting commands behind the ``ok``
    envelope.
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import rich_click as click
import tomlkit.exceptions
from rich.markup import escape
from rich.prompt import Confirm, Prompt

from lsdsk.adapters.config.offer import DisplayNotATableError, disable_offer
from lsdsk.adapters.shortcut.icon import copy_shipped_icon
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut
from lsdsk.adapters.shortcut.windows import WindowsDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult, uvx_path
from lsdsk.adapters.uv_bootstrap import install_uv as run_uv_installer
from lsdsk.domain.enums import ActionCommand, OutputFormat, ShortcutChoice
from lsdsk.domain.shortcut import command_line, consent_text, manual_install_line, plan_launch, should_offer

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS, FORMAT_OPTION
from ..envelope import ActionResult, emit_action, fail
from ..exit_codes import ExitCode
from ..typed_click import option

if TYPE_CHECKING:
    from collections.abc import Callable

    from lsdsk.adapters.config.tunables import DisplaySettings
    from lsdsk.application.ports import DesktopShortcuts


#: Said when an install reports success but uvx is still not resolvable in this
#: shell - a PATH that a new shell will pick up, shared by the command and the offer.
_UVX_STILL_MISSING = "uv was installed but uvx is still not on PATH; open a new shell and run 'lsdsk install-shortcut'."


def _installer_failed(exit_code: int) -> str:
    """What a non-zero installer is said as, by the command and the offer alike."""
    return (
        f"the uv installer failed (exit {exit_code}), so there is no launcher for the shortcut. "
        f"Install uv yourself with:\n\n  {manual_install_line(sys.platform)}\n\n"
        "then run 'lsdsk install-shortcut'."
    )


class InstallShortcutResult(ActionResult):
    """The shortcut that was written, and the command it launches.

    Attributes:
        shortcut: The path of the shortcut created or refreshed.
        launch: The argv the shortcut runs, so a caller can see what it will do.
    """

    shortcut: str
    launch: list[str]


@dataclass(frozen=True)
class ShortcutSeams:
    """The collaborators :func:`install_shortcut` and :func:`offer_shortcut` drive, as one injectable value.

    Attributes:
        shortcuts: The platform's shortcut writer.
        uvx: Resolves the ``uvx`` path, or ``None``.
        installed_lsdsk: Resolves a plain ``lsdsk`` launcher on PATH, or ``None``.
        install_uv: Installs uv and returns the installer's exit code with the
            ``uvx`` path it now resolves to, if any.
        copy_icon: Copies the shipped icon into place and returns its path; the real
            copier by default, so a test can inject one that raises to drive the
            offer's degrade-to-iconless path without touching the module.
    """

    shortcuts: DesktopShortcuts
    uvx: Callable[[], Path | None]
    installed_lsdsk: Callable[[], Path | None]
    install_uv: Callable[[], UvInstallResult]
    copy_icon: Callable[[], Path] = copy_shipped_icon


def _installed_lsdsk() -> Path | None:
    """A plain ``lsdsk`` launcher already on PATH, as an absolute path, or ``None``.

    Absolute because a relative ``PATH`` entry resolves relative to the directory
    lsdsk ran in, and the shortcut runs from the Desktop.
    """
    found = shutil.which("lsdsk")
    return Path(found).absolute() if found else None


def shortcuts_for(platform: str) -> DesktopShortcuts | None:
    """The shortcut adapter for ``platform``, or ``None`` where lsdsk has none.

    Linux, macOS and Windows are wired; any other platform is refused cleanly by
    the command rather than crashing.

    Args:
        platform: ``sys.platform`` of the running machine.

    Returns:
        The platform's shortcut adapter, or ``None`` when none is available yet.
    """
    if platform.startswith("linux"):
        return LinuxDesktopShortcut()
    if platform == "darwin":
        return MacosDesktopShortcut()
    if platform == "win32":
        return WindowsDesktopShortcut()
    return None


def _resolve_launch(seams: ShortcutSeams, *, install_uv: bool, output_format: OutputFormat) -> tuple[str, ...]:
    """The command the shortcut should run, installing uv first only when asked.

    Raises:
        SystemExit: When no launcher is available and ``--install-uv`` was not
            given, the installer failed, or an install still left ``uvx``
            unresolvable.
    """
    plan = plan_launch(uvx=seams.uvx(), installed_lsdsk=seams.installed_lsdsk())
    if not plan.needs_uv:
        return plan.argv
    if not install_uv:
        fail(
            f"uv is not installed, so there is no launcher for the shortcut.\n\n{consent_text(sys.platform)}\n\n"
            "Re-run with --install-uv to have lsdsk install uv for you first.",
            ExitCode.CONFIG_ERROR,
            output_format=output_format,
        )
    installed = seams.install_uv()
    if installed.exit_code != 0:
        fail(_installer_failed(installed.exit_code), ExitCode.CONFIG_ERROR, output_format=output_format)
    replan = plan_launch(uvx=installed.uvx, installed_lsdsk=seams.installed_lsdsk())
    if replan.needs_uv:
        fail(_UVX_STILL_MISSING, ExitCode.CONFIG_ERROR, output_format=output_format)
    return replan.argv


def _report(written: Path, launch: tuple[str, ...], output_format: OutputFormat, *, platform: str) -> None:
    """Say what was created, in the format the caller asked for; the launch line as ``platform`` reads it."""
    if output_format is OutputFormat.JSON:
        emit_action(ActionCommand.INSTALL_SHORTCUT, InstallShortcutResult(shortcut=str(written), launch=list(launch)))
        return
    safe_console.echo(f"\nShortcut created:\n  {written}")
    safe_console.echo(f"It launches: {command_line(launch, platform=platform)}")


def install_shortcut(
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    install_uv: bool = False,
    seams: ShortcutSeams | None = None,
    platform: str = sys.platform,
) -> None:
    """Create (or refresh) the desktop shortcut, resolving a launcher first.

    Args:
        output_format: Whether to report as human text or the JSON envelope.
        install_uv: Whether to install uv when no launcher is found.
        seams: The collaborators to drive; the real ones for ``platform`` when
            not given.
        platform: The ``sys.platform`` whose shortcut adapter is used when no
            ``seams`` are given; this machine's by default.

    Raises:
        SystemExit: On any refusal (unsupported platform, an account with no
            desktop folder, no launcher without ``--install-uv``, a failed
            install) and on a failed write, carrying the matching exit code.
    """
    if seams is None:
        shortcuts = shortcuts_for(platform)
        if shortcuts is None:
            fail(
                f"a desktop shortcut is not available on {platform} in this build yet",
                ExitCode.CONFIG_ERROR,
                output_format=output_format,
            )
        seams = ShortcutSeams(
            shortcuts=shortcuts, uvx=uvx_path, installed_lsdsk=_installed_lsdsk, install_uv=run_uv_installer
        )
    # Refused before uv could be installed for it: a headless server gains nothing from either.
    if not seams.shortcuts.has_desktop():
        fail(
            f"{seams.shortcuts.no_desktop_reason()}, so there is nowhere to put a shortcut",
            ExitCode.CONFIG_ERROR,
            output_format=output_format,
        )
    launch = _resolve_launch(seams, install_uv=install_uv, output_format=output_format)
    _report(
        _write_shortcut(seams, launch=launch, output_format=output_format), launch, output_format, platform=platform
    )


def _write_shortcut(seams: ShortcutSeams, *, launch: tuple[str, ...], output_format: OutputFormat) -> Path:
    """Copy the icon and write the shortcut, or fail with the code ``snapshot`` uses for its write.

    The explicit command does NOT degrade: a reader who asked for the shortcut is
    told when the icon or the write fails, unlike the best-effort offer. Left
    uncaught, the errno became the exit code, and a filesystem's numbers overlap
    the ones this tool means something by - ENOENT is 2, Click's usage error -
    and no JSON envelope was written at all.

    Raises:
        SystemExit: ``PERMISSION_DENIED`` when the write was refused for
            permission, ``IO_ERROR`` for any other failed write.
    """
    try:
        return seams.shortcuts.create(launch=launch, icon=seams.copy_icon())
    except PermissionError as error:
        fail(f"not allowed to write the shortcut: {error}", ExitCode.PERMISSION_DENIED, output_format=output_format)
    except OSError as error:
        fail(f"could not write the shortcut: {error}", ExitCode.IO_ERROR, output_format=output_format)


#: The three-way offer prompt, mapping the reader's letter to a choice.
_OFFER_ANSWERS = {"y": ShortcutChoice.CREATE, "n": ShortcutChoice.NOT_NOW, "d": ShortcutChoice.NEVER}


#: What a blank Enter, and end of input, answer at the shortcut question.
_OFFER_DEFAULT = "n"


def _ask_choice_prompt() -> ShortcutChoice:
    """Ask whether to create the shortcut: yes, not now, or never again.

    End of input (Ctrl+D) answers like a blank Enter, "not now": the question
    comes before the view the reader started, and leaving the input is no
    reason to abort that view.
    """
    # Escaped because Rich reads "[y]" as a markup tag and drops it, which left the
    # reader a list of answers with no letter to type for any of them.
    try:
        answer = Prompt.ask(
            escape(
                "Create a desktop shortcut that launches lsdsk? "
                "[y] yes, [n] not now, [d] do not ask again (sets display.offer_desktop_shortcut = false)"
            ),
            choices=list(_OFFER_ANSWERS),
            default=_OFFER_DEFAULT,
        )
    except EOFError:
        # The prompt line was left unfinished; end it so the next line starts clean.
        safe_console.echo("")
        return _OFFER_ANSWERS[_OFFER_DEFAULT]
    return _OFFER_ANSWERS[answer]


def _ask_install_prompt() -> bool:
    """Ask whether to install uv now, defaulting to no: a blank Enter or end of input does not install."""
    try:
        return Confirm.ask("Install uv automatically now?", default=False)
    except EOFError:
        safe_console.echo("")
        return False


def offer_shortcut(
    display: DisplaySettings,
    *,
    ask_choice: Callable[[], ShortcutChoice] | None = None,
    ask_install: Callable[[], bool] | None = None,
    seams: ShortcutSeams | None = None,
    disable: Callable[[], object] = disable_offer,
) -> None:
    """At a terminal, offer to create the desktop shortcut and act on the answer.

    Called only on the interactive path, before elevation, so the reader can
    answer. It stays silent when the config key is off, when the account has no
    desktop folder (a headless server), when the shortcut already exists, or on a
    platform whose adapter is not built yet; otherwise it asks the three-way
    question and creates, waits until next time, or writes the opt-out. Each line
    it prints after the question starts a paragraph of its own, so the questions
    that follow (uv's install, then sudo's) do not run on from it.

    Args:
        display: The layout settings; ``offer_desktop_shortcut`` gates the offer.
        ask_choice: Returns the reader's three-way answer; the real prompt by default.
        ask_install: Returns whether to install uv; the real prompt by default.
        seams: The collaborators; the real ones for this platform by default.
        disable: Writes the opt-out; ``disable_offer`` by default.
    """
    if seams is None:
        shortcuts = shortcuts_for(sys.platform)
        if shortcuts is None:
            return
        seams = ShortcutSeams(
            shortcuts=shortcuts, uvx=uvx_path, installed_lsdsk=_installed_lsdsk, install_uv=run_uv_installer
        )
    # at_terminal is True here: this is reached only on the interactive path.
    if not should_offer(
        at_terminal=True,
        offer_enabled=display.offer_desktop_shortcut,
        desktop_present=seams.shortcuts.has_desktop(),
        shortcut_present=seams.shortcuts.exists(),
    ):
        return
    choice = (ask_choice or _ask_choice_prompt)()
    if choice is ShortcutChoice.NEVER:
        _disable_for_the_offer(disable)
        return
    if choice is ShortcutChoice.NOT_NOW:
        return
    _offer_create(seams, ask_install=ask_install or _ask_install_prompt)


def _disable_for_the_offer(disable: Callable[[], object]) -> None:
    """Write the opt-out best-effort; a write that fails warns once and returns.

    The offer precedes the interactive view, so a read-only or full config
    directory must not abort it, and neither must a reader-hand-edited, malformed
    ``config.toml`` that the opt-out cannot safely edit in place: the opt-out is a
    courtesy, not the run's work. The default ``disable`` (``disable_offer``)
    raises ``OSError`` for the unwritable directory, a ``tomlkit``/``tomllib``
    parse error for the malformed file and ``DisplayNotATableError`` for a
    ``display`` that is not a table, and all of them degrade the same way. A
    programming error (any other exception) still propagates.
    """
    try:
        disable()
    except (OSError, DisplayNotATableError, tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError) as exc:
        safe_console.echo(f"\nCould not save the do-not-offer setting: {exc}", err=True)
        return
    safe_console.echo("\nlsdsk will not offer the shortcut again; run 'lsdsk install-shortcut' to create it later.")


def _offer_create(seams: ShortcutSeams, *, ask_install: Callable[[], bool]) -> None:
    """Resolve a launcher (installing uv on consent), then create the shortcut.

    Best-effort, unlike the explicit command: this runs before the interactive
    view opens, so an unwritable config directory must not abort the view. An
    icon that will not copy degrades to an iconless shortcut, and a create that
    fails warns once and returns.
    """
    plan = plan_launch(uvx=seams.uvx(), installed_lsdsk=seams.installed_lsdsk())
    if plan.needs_uv:
        safe_console.echo(f"\n{consent_text(sys.platform)}\n")
        if not ask_install():
            return
        installed = seams.install_uv()
        if installed.exit_code != 0:
            safe_console.echo(f"\n{_installer_failed(installed.exit_code)}", err=True)
            return
        plan = plan_launch(uvx=installed.uvx, installed_lsdsk=seams.installed_lsdsk())
        if plan.needs_uv:
            safe_console.echo(f"\n{_UVX_STILL_MISSING}")
            return
    _create_for_the_offer(seams, launch=plan.argv)


def _create_for_the_offer(seams: ShortcutSeams, *, launch: tuple[str, ...]) -> None:
    """Create the shortcut best-effort, never letting a filesystem error abort the view."""
    try:
        written = seams.shortcuts.create(launch=launch, icon=_icon_or_none(seams.copy_icon))
    except OSError as exc:
        safe_console.echo(f"\nCould not create the desktop shortcut: {exc}", err=True)
        return
    safe_console.echo(f"\nShortcut created at {written}.")


def _icon_or_none(copy_icon: Callable[[], Path]) -> Path | None:
    """The copied icon, or ``None`` when the copy fails - an iconless shortcut still launches lsdsk."""
    try:
        return copy_icon()
    except OSError:
        return None


@click.command("install-shortcut", context_settings=CLICK_CONTEXT_SETTINGS)
@FORMAT_OPTION
@option(
    "--install-uv",
    "install_uv",
    is_flag=True,
    default=False,
    help="If no launcher is found, install uv first from the official installer (makes a network request).",
)
def cli_install_shortcut(*, output_format: OutputFormat = OutputFormat.HUMAN, install_uv: bool = False) -> None:
    """Create a desktop shortcut that launches lsdsk.

    The shortcut runs lsdsk through uvx (with a cooldown) when it is installed,
    otherwise through a plain lsdsk already on PATH. If neither is present, pass
    --install-uv to install uv first.
    """
    install_shortcut(output_format=output_format, install_uv=install_uv)


__all__ = [
    "InstallShortcutResult",
    "ShortcutSeams",
    "cli_install_shortcut",
    "install_shortcut",
    "offer_shortcut",
    "shortcuts_for",
]
