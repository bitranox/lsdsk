"""Linux hardware readers.

Split in two so the mapping logic can be tested anywhere:
    * :mod:`.reader` - the impure half, reading sysfs and issuing ioctls
    * :mod:`.builder` - the pure half, turning a reading into domain objects
"""

from __future__ import annotations

from .builder import build_inventory

__all__ = ["build_inventory"]
