"""Resolve numeric PCI vendor and device identifiers to readable names.

This is a lookup, not a reading, and it is the only piece of information the
tool cannot get from the hardware itself.  It never reaches the network: it
reads the system's own ``pci.ids`` where there is one, falls back to a copy
bundled with the package, and where a device is in neither, a small built-in
table of storage vendors keeps the output readable.

The bundled copy is why a controller is named the same on every platform. The
alternative was the operating system's own device description, which Windows
localises: on a German install an NVMe controller reads "Standardmaessiger NVM
Express-Controller", so the output was half English and half not, and two
machines running the same hardware disagreed about what was in them.

To refresh the bundled copy, rebuild ``pci.ids.gz`` from a current upstream
``pci.ids``: keep its comment header, which carries the licence and the version
date, keep every unindented vendor line and every one-tab device line, and drop
the two-tab subsystem lines and the trailing class section, neither of which
:func:`parse_pci_ids` reads. Compress with ``mtime=0`` so an unchanged input
produces a byte-identical file rather than a diff on every rebuild.

System Role:
    Pure adapter-layer lookup over a local data file.
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from ...textfile import read_text_bounded

# Ships with the package, so a machine with no hwdata installed - every Windows
# one - still resolves a controller to the same name Linux gives it.
BUNDLED_PCI_IDS = Path(__file__).with_name("pci.ids.gz")

# Distributions disagree on where the hwdata package puts this file, so try the
# places it is actually found rather than assuming one.
PCI_IDS_SEARCH_PATHS: tuple[str, ...] = (
    "/usr/share/misc/pci.ids",
    "/usr/share/hwdata/pci.ids",
    "/usr/share/pci.ids",
    "/var/lib/pciutils/pci.ids",
    "/opt/homebrew/share/pci.ids",
)

# Enough vendors to keep a controller listing readable on a machine with no
# pci.ids at all. Storage silicon is a small field.
FALLBACK_VENDORS: dict[int, str] = {
    0x1000: "Broadcom / LSI",
    0x1002: "AMD",
    0x1022: "AMD",
    0x1055: "Microchip",
    0x1077: "QLogic",
    0x1095: "Silicon Image",
    0x1103: "HighPoint",
    0x105A: "Promise",
    0x11AB: "Marvell",
    0x1344: "Micron",
    0x144D: "Samsung",
    0x14E4: "Broadcom",
    0x15B7: "Sandisk",
    0x1987: "Phison",
    0x1B21: "ASMedia",
    0x1B4B: "Marvell",
    0x1BB1: "Seagate",
    0x1C5C: "SK hynix",
    0x1CC1: "ADATA",
    0x1D97: "Shenzhen Longsys",
    0x1DEE: "Biwin",
    0x1E0F: "KIOXIA",
    0x1E4B: "MAXIO",
    0x2646: "Kingston",
    0x8086: "Intel",
    0x9005: "Adaptec",
}


def find_pci_ids(search_paths: tuple[str, ...] = PCI_IDS_SEARCH_PATHS) -> Path | None:
    """Return the first readable ``pci.ids`` on this system.

    Args:
        search_paths: Candidate locations, in preference order.

    Returns:
        The path, or ``None`` when the system has no such database.

    Example:
        >>> find_pci_ids(("/definitely/not/here",)) is None
        True
    """
    for candidate in search_paths:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def parse_pci_ids(text: str) -> Database:
    """Parse a ``pci.ids`` database.

    The format nests by indentation: an unindented line opens a vendor, one tab
    of indent gives a device under it, and two tabs give a subsystem, which this
    parser skips because the controller's own name is what gets displayed.

    Args:
        text: The database contents.

    Returns:
        A vendor-name map and a (vendor, device)-name map.

    Example:
        >>> vendors, devices = parse_pci_ids("1000  Broadcom\\n\\t0097  SAS3008\\n")
        >>> vendors[0x1000]
        'Broadcom'
        >>> devices[(0x1000, 0x0097)]
        'SAS3008'
    """
    vendors: dict[int, str] = {}
    devices: dict[tuple[int, int], str] = {}
    current_vendor: int | None = None

    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("\t\t"):
            continue
        stripped = line.lstrip("\t")
        identifier, separator, name = stripped.partition("  ")
        if not separator:
            continue
        try:
            value = int(identifier, 16)
        except ValueError:
            # Device class and programming interface sections follow the vendor
            # list and are keyed by letters, not hex, so they end vendor parsing.
            current_vendor = None
            continue
        if line.startswith("\t"):
            if current_vendor is not None:
                devices[(current_vendor, value)] = name.strip()
        else:
            current_vendor = value
            vendors[value] = name.strip()
    return Database(vendors, devices)


def read_bundled_pci_ids(path: Path = BUNDLED_PCI_IDS) -> str | None:
    """Return the bundled database's text, or ``None`` if it cannot be read.

    Args:
        path: The compressed database to read.

    Returns:
        The decompressed contents, or ``None`` when the file is absent or
        unreadable.

    Example:
        >>> read_bundled_pci_ids(Path("/definitely/not/here.gz")) is None
        True
    """
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
    except (OSError, gzip.BadGzipFile, EOFError):
        return None


def _database_text() -> str | None:
    """The best available database contents: the system's, else the bundled one.

    The system file wins when there is one, because a distribution's hwdata
    package is refreshed more often than a release of this tool, so it knows
    about newer silicon.
    """
    # Passed explicitly rather than left to the default argument, which binds
    # the tuple at definition time: with the default, substituting the search
    # paths cannot reach this call, and a test that removes every path still
    # reads the real system file and passes having proved nothing.
    path = find_pci_ids(PCI_IDS_SEARCH_PATHS)
    if path is not None:
        try:
            return read_text_bounded(path, what="a PCI ID database", errors="replace")
        except OSError:
            pass
    return read_bundled_pci_ids()


def reset_database_cache() -> None:
    """Forget the loaded database so the next lookup reads it again.

    The database is read once and cached, which is right for a process that
    reports one machine. Anything that changes what would be read - a different
    set of search paths, a file appearing or going away - has to say so, and
    without this the only way to say it is to reach into a private cache.
    """
    _load_database.cache_clear()


@lru_cache(maxsize=1)
def _load_database() -> Database:
    """Load and cache the PCI identifier database."""
    text = _database_text()
    if text is None:
        return Database(dict(FALLBACK_VENDORS), {})
    vendors, devices = parse_pci_ids(text)
    for identifier, name in FALLBACK_VENDORS.items():
        vendors.setdefault(identifier, name)
    return Database(vendors, devices)


class Database(NamedTuple):
    """The pci.ids tables: vendors by id, devices by (vendor, device).

    Both are dicts, so returned as a bare pair a swap type-checks and then every
    lookup silently misses, leaving every device unnamed rather than failing.

    Attributes:
        vendors: Vendor id to name.
        devices: (vendor id, device id) to name.
    """

    vendors: dict[int, str]
    devices: dict[tuple[int, int], str]


def lookup_vendor(vendor: int, database: Database | None = None) -> str | None:
    """Return a vendor's name, or ``None`` when it is not in the database."""
    vendors, _ = database if database is not None else _load_database()
    return vendors.get(vendor)


def lookup_device(vendor: int, device: int, database: Database | None = None) -> str | None:
    """Return a device's name, or ``None`` when it is not in the database."""
    _, devices = database if database is not None else _load_database()
    return devices.get((vendor, device))


def describe(vendor: int, device: int, database: Database | None = None) -> str:
    """Return the best readable name for a PCI device.

    Falls back through vendor-only naming to bare hex, so the caller always gets
    something printable.

    Args:
        vendor: PCI vendor identifier.
        device: PCI device identifier.
        database: A parsed database to use instead of the system one. Supplying
            it keeps callers and tests independent of whether this machine has a
            ``pci.ids`` at all, and of which release of it.

    Returns:
        A display name.

    Example:
        >>> known = ({0x1000: "Broadcom"}, {(0x1000, 0x0097): "SAS3008"})
        >>> describe(0x1000, 0x0097, known)
        'Broadcom SAS3008'
        >>> describe(0x1000, 0xABCD, known)
        'Broadcom device abcd'
        >>> describe(0xFFFE, 0xFFFE, ({}, {}))
        'Device fffe:fffe'

    A snapshot records already-resolved names with the vendor folded in and no
    separate vendor map, so a device name alone is enough and must not be
    discarded for want of one:

        >>> describe(0x144D, 0xA80C, ({}, {(0x144D, 0xA80C): "Samsung NVMe SSD"}))
        'Samsung NVMe SSD'
    """
    vendor_name = lookup_vendor(vendor, database)
    device_name = lookup_device(vendor, device, database)
    if device_name:
        return f"{vendor_name} {device_name}" if vendor_name else device_name
    if vendor_name:
        return f"{vendor_name} device {device:04x}"
    return f"Device {vendor:04x}:{device:04x}"


__all__ = [
    "BUNDLED_PCI_IDS",
    "FALLBACK_VENDORS",
    "PCI_IDS_SEARCH_PATHS",
    "Database",
    "describe",
    "find_pci_ids",
    "lookup_device",
    "lookup_vendor",
    "parse_pci_ids",
    "read_bundled_pci_ids",
    "reset_database_cache",
]
