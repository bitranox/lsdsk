"""Copy the shipped icon into the stable per-user directory the shortcut names.

A ``uvx lsdsk@latest`` launch reinstalls lsdsk into an ephemeral directory, so a
shortcut that pointed at an icon inside the installed package would lose it on
the next upgrade. The shortcut therefore references a copy in the per-user
configuration directory - the one place that outlives an upgrade - and this
module writes that copy, refreshing it on every create so an upgraded package's
icon replaces the old one.

The source is resolved through :mod:`importlib.resources` rather than a path
built from ``__file__``, because the package may be installed read-only or
zipped. The destination directory is the one :func:`user_config_path` names,
so no path is guessed here, and the write goes through the shared atomic writer,
so a failed copy leaves any previous icon untouched.

System Role:
    Adapter-layer file-writing boundary; prepares the icon the desktop-shortcut
    adapters reference.

Contents:
    * :func:`copy_shipped_icon` - resolve the packaged icon and copy it into place.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

from lsdsk.adapters.atomicfile import replace_bytes_atomically
from lsdsk.adapters.config.offer import user_config_path

if TYPE_CHECKING:
    from pathlib import Path

#: The icon as it is shipped inside the package.
_SHIPPED_ICON = "lsdsk-256.png"

#: The name of the copy the shortcut references, beside the user ``config.toml``.
_USER_ICON_NAME = "lsdsk.png"

#: An icon is not a secret, so unlike the configuration beside it the copy is
#: world-readable - a desktop environment reads it to draw the launcher.
_ICON_MODE = 0o644


def copy_shipped_icon() -> Path:
    """Copy the packaged icon into the per-user directory and return its path.

    The copy lands beside the user ``config.toml`` and is rewritten on every
    call, so a package upgrade's new icon propagates to the shortcut.

    Returns:
        The path of the copied icon.

    Raises:
        OSError: The icon could not be written - a full disk or a permission
            wall. Any previous copy is left untouched.
        NoUserConfigPathError: No user configuration directory can be named on
            this machine (an ``OSError``, so it is a failed write like the rest).
    """
    body = (resources.files("lsdsk") / "assets" / "icon" / _SHIPPED_ICON).read_bytes()
    destination = user_config_path().parent / _USER_ICON_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    replace_bytes_atomically(destination, body, mode=_ICON_MODE)
    return destination


__all__ = ["copy_shipped_icon"]
