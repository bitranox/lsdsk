"""Per-platform desktop-shortcut adapters.

Each module creates the shortcut its platform understands - a Linux
``.desktop`` file, a macOS bundle, a Windows ``.lnk`` through pwshpy - behind the
one :class:`~lsdsk.application.ports.DesktopShortcuts` port, so the orchestration
above names a platform nowhere and a test injects a fake at the seam.
"""

from __future__ import annotations
