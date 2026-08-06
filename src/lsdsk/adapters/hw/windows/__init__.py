"""Windows hardware readers.

Split in two so the mapping logic can be tested anywhere:
    * :mod:`.reader` - the impure half, SetupAPI and DeviceIoControl
    * :mod:`.builder` - the pure half, turning a reading into domain objects
    * :mod:`.winapi` - the ctypes structures and constants those use

Only :mod:`.builder` is importable off Windows; the other two bind Windows DLLs.
"""

from __future__ import annotations

from .builder import build_inventory

__all__ = ["build_inventory"]
