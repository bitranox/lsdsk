"""Hardware inspection adapters.

Layout:
    * :mod:`.decode` - pure ``bytes`` to model decoders, shared by every platform
    * :mod:`.linux` - sysfs readers and ioctl transports
    * :mod:`.windows` - SetupAPI and DeviceIoControl transports
    * :mod:`.snapshot` - capture an inventory to JSON and replay it

The split matters: Linux and Windows deliver the same ATA and NVMe structures
over different transports, so only the transports are platform-specific and the
decoding is tested on every runner regardless of the platform it runs on.
"""

from __future__ import annotations

# A package docstring and nothing else: the submodules are imported by path, so
# this package re-exports none of them. Stated rather than left absent, because
# every other package here states it and a missing __all__ reads as an oversight.
__all__: list[str] = []
