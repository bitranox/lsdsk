"""The decisions the real-hardware run makes about the fleet, kept where they can be tested.

``test_real_hardware`` needs the fleet and its key, so nothing in it runs on CI.
What it DECIDES - where uv lives on a Windows host, and whether the Windows
bitmap walk was really exercised - is pure, and lives here so ``test_fleet``
holds it on every runner.

System Role:
    Test support for ``tests/e2e``. Not part of the package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "FRAGMENTATION_CHECK",
    "WINDOWS_UV_CANDIDATES",
    "first_uv_path",
    "windows_fragmentation_gaps",
    "windows_uv_lookup_command",
]

#: The probe check that walks the NTFS volume bitmaps. It returns no check at
#: all when a host shows no NTFS row, so its absence has to be looked for.
FRAGMENTATION_CHECK: Final = "filesystems:fragmentation-read"

#: Where uv is installed on a Windows host when the ssh session's PATH does not
#: name it, in the order they are tried: the standalone installer's
#: machine-wide location, winget's machine-wide and per-user links, and the two
#: per-user install locations. ``%VAR%`` is expanded by the remote cmd.exe, so
#: each names the account the session runs as. Measured on a Windows host whose
#: uv came from winget: ``where`` found it, and with PATH cut to system32 the
#: machine-wide winget entry here found it instead.
WINDOWS_UV_CANDIDATES: Final[tuple[str, ...]] = (
    "C:\\Program Files\\uv\\uv.exe",
    "C:\\Program Files\\WinGet\\Links\\uv.exe",
    "%LOCALAPPDATA%\\Microsoft\\WinGet\\Links\\uv.exe",
    "%USERPROFILE%\\.local\\bin\\uv.exe",
    "%USERPROFILE%\\.cargo\\bin\\uv.exe",
)


def windows_uv_lookup_command() -> str:
    """The cmd.exe line that prints every uv.exe a Windows host has, PATH first.

    ``where`` answers the way ``shutil.which`` would on that host; each known
    location follows, printed only when it exists. Each ``if`` is parenthesised
    because cmd.exe otherwise reads the ``&`` after it as part of the ``if``'s
    own command, so a missing first location would silence every later one.

    Returns:
        One command line for the host's default ssh shell.

    Examples:
        >>> windows_uv_lookup_command().startswith("where uv 2>nul & ")
        True
    """
    checks = " & ".join(f'(if exist "{path}" echo {path})' for path in WINDOWS_UV_CANDIDATES)
    return f"where uv 2>nul & {checks}"


def first_uv_path(printed: str) -> str | None:
    r"""The first uv.exe the lookup printed, or None when it found none.

    Args:
        printed: The lookup's stdout, CRLF line endings and all.

    Returns:
        The path to run, or None, never a guessed default.

    Examples:
        >>> first_uv_path("C:\\tools\\uv.exe\r\n")
        'C:\\tools\\uv.exe'
        >>> first_uv_path("") is None
        True
    """
    for line in printed.splitlines():
        candidate = line.strip()
        if candidate.lower().endswith("uv.exe"):
            return candidate
    return None


def windows_fragmentation_gaps(probed: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each Windows host whose run never judged the NTFS bitmap walk.

    The per-host test fails a fragmentation check that ran and failed, but the
    check is not emitted at all on a host with no NTFS row, and a check that is
    absent cannot fail. So the fleet asks for its NAME among the host's checks,
    and a host recorded without check names is a gap too rather than a pass.

    Args:
        probed: One facts mapping per host the run reached.

    Returns:
        One line per Windows host that lacks the check, empty when none does.
    """
    gaps: list[str] = []
    for facts in probed:
        if str(facts.get("platform")) != "Windows":
            continue
        names = facts.get("check_names") or ()
        if FRAGMENTATION_CHECK in names:
            continue
        gaps.append(
            f"{facts.get('host')}: no {FRAGMENTATION_CHECK} check ran "
            f"({facts.get('ntfs_volumes', 0)} NTFS volume(s), privileged={facts.get('privileged')})"
        )
    return gaps
