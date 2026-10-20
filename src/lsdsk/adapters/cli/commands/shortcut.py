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

import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import rich_click as click

from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.uv_bootstrap import install_uv as run_uv_installer
from lsdsk.adapters.uv_bootstrap import uvx_path
from lsdsk.domain.enums import ActionCommand, OutputFormat
from lsdsk.domain.shortcut import consent_text, plan_launch

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS, FORMAT_OPTION
from ..envelope import ActionResult, emit_action, fail
from ..exit_codes import ExitCode
from ..typed_click import option

if TYPE_CHECKING:
    from collections.abc import Callable

    from lsdsk.application.ports import DesktopShortcuts


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
    """The collaborators :func:`install_shortcut` drives, as one injectable value.

    Attributes:
        shortcuts: The platform's shortcut writer.
        uvx: Resolves the ``uvx`` path, or ``None``.
        installed_lsdsk: Resolves a plain ``lsdsk`` launcher on PATH, or ``None``.
        install_uv: Installs uv and returns the ``uvx`` path if it is now resolvable.
    """

    shortcuts: DesktopShortcuts
    uvx: Callable[[], Path | None]
    installed_lsdsk: Callable[[], Path | None]
    install_uv: Callable[[], Path | None]


def _installed_lsdsk() -> Path | None:
    """A plain ``lsdsk`` launcher already on PATH, or ``None``."""
    found = shutil.which("lsdsk")
    return Path(found) if found else None


def shortcuts_for(platform: str) -> DesktopShortcuts | None:
    """The shortcut adapter for ``platform``, or ``None`` where none exists yet.

    Only Linux is wired so far; macOS and Windows adapters arrive with their own
    steps, and until then the command refuses cleanly rather than crashing.
    """
    if platform.startswith("linux"):
        return LinuxDesktopShortcut()
    return None


def _resolve_launch(seams: ShortcutSeams, *, install_uv: bool, output_format: OutputFormat) -> tuple[str, ...]:
    """The command the shortcut should run, installing uv first only when asked.

    Raises:
        SystemExit: When no launcher is available and ``--install-uv`` was not
            given, or an install still left ``uvx`` unresolvable.
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
    replan = plan_launch(uvx=seams.install_uv(), installed_lsdsk=seams.installed_lsdsk())
    if replan.needs_uv:
        fail(
            "uv was installed but uvx is still not on PATH; open a new shell and run install-shortcut again.",
            ExitCode.SOFTWARE_ERROR,
            output_format=output_format,
        )
    return replan.argv


def _report(written: Path, launch: tuple[str, ...], output_format: OutputFormat) -> None:
    """Say what was created, in the format the caller asked for."""
    if output_format is OutputFormat.JSON:
        emit_action(ActionCommand.INSTALL_SHORTCUT, InstallShortcutResult(shortcut=str(written), launch=list(launch)))
        return
    safe_console.echo(f"\nShortcut created:\n  {written}")
    safe_console.echo(f"It launches: {shlex.join(launch)}")


def install_shortcut(
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    install_uv: bool = False,
    seams: ShortcutSeams | None = None,
) -> None:
    """Create (or refresh) the desktop shortcut, resolving a launcher first.

    Args:
        output_format: Whether to report as human text or the JSON envelope.
        install_uv: Whether to install uv when no launcher is found.
        seams: The collaborators to drive; the real ones for this platform when
            not given.

    Raises:
        SystemExit: On any refusal (unsupported platform, no launcher without
            ``--install-uv``, a failed install), carrying the matching exit code.
    """
    if seams is None:
        shortcuts = shortcuts_for(sys.platform)
        if shortcuts is None:
            fail(
                f"a desktop shortcut is not available on {sys.platform} in this build yet",
                ExitCode.CONFIG_ERROR,
                output_format=output_format,
            )
        seams = ShortcutSeams(
            shortcuts=shortcuts, uvx=uvx_path, installed_lsdsk=_installed_lsdsk, install_uv=run_uv_installer
        )
    launch = _resolve_launch(seams, install_uv=install_uv, output_format=output_format)
    written = seams.shortcuts.create(launch=launch, icon=None)
    _report(written, launch, output_format)


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


__all__ = ["InstallShortcutResult", "ShortcutSeams", "cli_install_shortcut", "install_shortcut", "shortcuts_for"]
