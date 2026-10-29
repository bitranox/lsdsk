"""The Windows desktop shortcut: a ``.lnk`` that starts lsdsk as Administrator, written through pwshpy.

lsdsk reads SMART and a SATA drive's link speed through passthrough commands
that need Administrator rights. A shortcut that starts it plainly would ask for
them through a UAC relaunch, which opens the view in a second window while the
first one waits. The ``.lnk`` therefore carries the run-as-administrator flag:
Windows asks once, before lsdsk starts, and the elevated process skips its own
relaunch - one prompt, one window. Cancelling that prompt starts nothing, which
is how Windows treats every run-as shortcut.

The Desktop is the folder Windows itself names for this account (a redirected or
OneDrive Desktop included), asked through pwshpy's known-folder lookup, and it is
never created here. The same shortcut also goes into the Start-menu Programs
folder, so lsdsk is found by name, as the Linux entry is in the application menu.
The Desktop ``.lnk`` is the shortcut and the Start-menu copy the extra: a Start
menu Windows names none of is skipped, and one that refuses the copy is a
warning about a shortcut that does exist rather than a failure to make one.

pwshpy is reached by name through a narrow typed facade, as
:mod:`lsdsk.adapters.hw.elevation` reaches it, and the loader is a keyword so a
test injects a fake module. This module calls only the shortcut and known-folder
members: writing a shortcut starts no process.

System Role:
    Adapter-layer file-writing boundary; satisfies
    :class:`~lsdsk.application.ports.DesktopShortcuts` on Windows.
"""

from __future__ import annotations

import errno
import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, Unpack, cast

from lsdsk.adapters.shortcut.probe import probe
from lsdsk.domain.shortcut import SHORTCUT_COMMENT

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_LOGGER = logging.getLogger(__name__)

_FILENAME = "lsdsk.lnk"


class ShortcutWriteError(OSError):
    """pwshpy refused or failed to write the ``.lnk``; an ``OSError`` so a failed write is handled as one."""


class ShortcutAccessDeniedError(ShortcutWriteError, PermissionError):
    """Windows refused the ``.lnk`` for lack of rights; a ``PermissionError``, so the command exits 13 for it.

    A subclass of ``OSError`` built with ``EACCES`` stays that subclass rather
    than becoming a ``PermissionError`` (only a bare ``OSError`` maps its errno to
    a subclass), so the class itself has to be one.
    """


class ShortcutFields(TypedDict):
    """Everything a ``.lnk`` carries besides its own path, named as pwshpy's ``new_shortcut`` takes it.

    One value, so the Desktop and the Start-menu shortcut are written from the
    same fields and cannot launch two different things.
    """

    target: str
    arguments: tuple[str, ...]
    working_directory: str
    icon: str | None
    description: str
    force: bool
    run_as_administrator: bool


class _Shortcuts(Protocol):
    """The members of pwshpy's ``ps`` facade this module calls."""

    def get_known_folder(self, folder: object) -> Path | None:
        """The folder's path on this machine, or None where it has none."""
        ...

    def new_shortcut(self, path: str, /, **fields: Unpack[ShortcutFields]) -> object:
        """Write the ``.lnk`` and return what Windows stored."""
        ...


class _KnownFolders(Protocol):
    """The two members of pwshpy's ``KnownFolder`` enum this module asks for."""

    @property
    def DESKTOP(self) -> object: ...  # noqa: N802 - pwshpy's own member name

    @property
    def PROGRAMS(self) -> object: ...  # noqa: N802 - pwshpy's own member name


class PwshpyShortcutModule(Protocol):
    """The part of the pwshpy module the shortcut relies on.

    Attributes:
        ps: The facade carrying ``get_known_folder`` and ``new_shortcut``.
        KnownFolder: The folder names ``get_known_folder`` takes.
        PwshPyError: The root of pwshpy's errors.
    """

    @property
    def ps(self) -> _Shortcuts: ...  # noqa: D102 - a Protocol member, documented above

    @property
    def KnownFolder(self) -> _KnownFolders: ...  # noqa: D102, N802 - pwshpy's own class name

    @property
    def PwshPyError(self) -> type[Exception]: ...  # noqa: D102, N802 - pwshpy's own class name


def _installed_pwshpy() -> PwshpyShortcutModule:
    """Import pwshpy by name, as the elevation relaunch does."""
    return cast("PwshpyShortcutModule", importlib.import_module("pwshpy"))


class WindowsDesktopShortcut:
    """Create and find the ``lsdsk.lnk`` launcher on the reader's Desktop."""

    def __init__(
        self, *, load: Callable[[], PwshpyShortcutModule] | None = None, warn: Callable[[str], None] | None = None
    ) -> None:
        """Reach pwshpy through ``load`` and report a skipped Start-menu copy through ``warn``.

        Args:
            load: Returns the pwshpy module; the installed one when not given.
            warn: Told, in one sentence, when the Desktop shortcut was written
                and the Start-menu copy was refused; a logged warning when not given.
        """
        self._load = load or _installed_pwshpy
        self._warn = warn or _LOGGER.warning
        self._module: PwshpyShortcutModule | None = None
        self._lookup_error: str | None = None

    def _pwshpy(self) -> PwshpyShortcutModule:
        """The pwshpy module, loaded on first use so no other platform pays for it."""
        if self._module is None:
            self._module = self._load()
        return self._module

    def _known_folder(self, choose: Callable[[_KnownFolders], object]) -> Path | None:
        """The folder ``choose`` picks, or None where Windows names none or the lookup failed."""
        pwshpy = self._pwshpy()
        try:
            return pwshpy.ps.get_known_folder(choose(pwshpy.KnownFolder))
        except pwshpy.PwshPyError as exc:
            self._lookup_error = str(exc)
            return None

    def desktop_dir(self) -> Path | None:
        """The Desktop folder Windows names for this account, or None where it names none."""
        return self._known_folder(lambda folders: folders.DESKTOP)

    def has_desktop(self) -> bool:
        """Whether Windows names a Desktop folder and it exists; it is never created here."""
        desktop = self.desktop_dir()
        return desktop is not None and probe(desktop.is_dir)

    def exists(self) -> bool:
        """Whether the Desktop already carries the shortcut."""
        desktop = self.desktop_dir()
        return desktop is not None and probe((desktop / _FILENAME).is_file)

    def no_desktop_reason(self) -> str:
        """Why there is no desktop: a failed lookup, no folder named, or a named folder that is missing."""
        desktop = self.desktop_dir()
        if self._lookup_error is not None:
            return f"Windows could not name the Desktop folder ({self._lookup_error})"
        if desktop is None:
            return "Windows names no Desktop folder for this account"
        return f"no desktop folder at {desktop}"

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        """Write the run-as ``.lnk`` to the Desktop, and to the Start menu where Windows names one.

        A Start-menu copy that is refused is reported through ``warn`` and does
        not fail the call, because the Desktop shortcut it returns is already there.

        Args:
            launch: The command the shortcut runs, as an argv; the first item is
                the executable, an absolute path.
            icon: An ``.ico`` file to show, or ``None`` for the target's own icon.

        Returns:
            The path of the Desktop shortcut.

        Raises:
            FileNotFoundError: When there is no Desktop folder; it is never
                created here, so nothing is written at all.
            ShortcutAccessDeniedError: Windows refused to write the Desktop
                shortcut for lack of rights; a ``PermissionError``.
            ShortcutWriteError: The launch is empty, or pwshpy refused or failed
                to write the Desktop shortcut for any other reason.
        """
        if not launch:
            raise ShortcutWriteError(errno.EINVAL, "a shortcut needs a command to run, and the launch is empty")
        desktop = self.desktop_dir()
        if desktop is None or not probe(desktop.is_dir):
            raise FileNotFoundError(errno.ENOENT, self.no_desktop_reason(), str(desktop) if desktop else None)
        fields = ShortcutFields(
            target=launch[0],
            arguments=tuple(launch[1:]),
            working_directory=str(Path.home()),
            icon=str(icon) if icon is not None else None,
            description=SHORTCUT_COMMENT,
            force=True,
            run_as_administrator=True,
        )
        written = desktop / _FILENAME
        self._write(written, fields)
        self._add_to_the_start_menu(fields)
        return written

    def _add_to_the_start_menu(self, fields: ShortcutFields) -> None:
        """Write the same ``.lnk`` into the Start menu where Windows names one, warning rather than raising."""
        menu = self._known_folder(lambda folders: folders.PROGRAMS)
        if menu is None or not probe(menu.is_dir):
            return
        try:
            self._write(menu / _FILENAME, fields)
        except OSError as exc:
            self._warn(f"the shortcut is on the Desktop, but it could not be added to the Start menu: {exc}")

    def _write(self, path: Path, fields: ShortcutFields) -> None:
        """Write one ``.lnk`` at ``path`` from ``fields``, replacing any there."""
        pwshpy = self._pwshpy()
        try:
            pwshpy.ps.new_shortcut(str(path), **fields)
        # pwshpy's NativeAccessDeniedError is a PwshPyError AND a PermissionError, so this arm must
        # come first. It is caught by the stdlib type: the facade names only pwshpy's root error.
        except PermissionError as exc:
            raise ShortcutAccessDeniedError(errno.EACCES, f"not allowed to write {path}: {exc}") from exc
        except pwshpy.PwshPyError as exc:
            raise ShortcutWriteError(errno.EIO, f"could not write {path}: {exc}") from exc


__all__ = [
    "PwshpyShortcutModule",
    "ShortcutAccessDeniedError",
    "ShortcutFields",
    "ShortcutWriteError",
    "WindowsDesktopShortcut",
]
