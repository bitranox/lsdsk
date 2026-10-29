"""Ask whether a shortcut folder or file is there, reading an unreachable one as absent.

Every desktop-shortcut adapter asks the same two questions of the filesystem -
is the Desktop a folder, is the shortcut a file - and needs the same answer when
a parent folder refuses the account: no. ``Path.is_dir`` and ``Path.is_file``
re-raise that ``PermissionError`` before Python 3.14 and answer ``False`` from
3.14 on, so without this the same machine reported "no desktop" on one
interpreter and crashed the command on another.

System Role:
    Adapter-layer filesystem probe shared by the Linux, macOS and Windows
    desktop-shortcut adapters.

Contents:
    * :func:`probe` - run a filesystem check, an ``OSError`` read as no.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def probe(check: Callable[[], bool]) -> bool:
    """``check()``, an ``OSError`` read as no: a folder this account cannot reach holds no shortcut.

    Args:
        check: The filesystem question, such as a bound ``Path.is_dir``.

    Returns:
        What ``check`` answered, or ``False`` when asking it raised ``OSError``.

    Example:
        >>> from pathlib import Path
        >>> def refused() -> bool:
        ...     raise PermissionError(13, "Permission denied")
        >>> probe(refused)
        False
        >>> probe(Path(".").is_dir)
        True
    """
    try:
        return check()
    except OSError:
        return False


__all__ = ["probe"]
