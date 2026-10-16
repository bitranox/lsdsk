"""Resolve what uses each Windows disk, from what the reader recorded.

Pure: a capture in, one ``DiskUsage`` per disk out. It also holds the two
volume rules the reader and the filesystems builder must apply alike, which
is why both import them from here rather than each keeping a copy: the order
a volume's paths are named in (:func:`named_first`) and whether a volume can
be a row (:func:`can_be_a_row`).

System Role:
    Adapter layer, translation half of the Windows mapping path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from ....domain.enums import Environment, UseKind
from ....domain.models import DiskUsage, DiskUse
from . import winapi as api

if TYPE_CHECKING:
    from collections.abc import Container, Sequence

    from .capture import VolumeEntry, WindowsCapture

#: A disk's node, as :mod:`.builder` names it. A fixture capture's
#: ``DiskEntry.node`` is always already in this shape (confirmed against
#: the committed Windows fixtures), so this reads the number back out rather
#: than re-deriving the name from the interface path the way the builder does.
_NODE_NUMBER = re.compile(r"PhysicalDrive(\d+)$", re.IGNORECASE)
#: A drive-letter root, the name a reader knows a volume by.
_DRIVE_LETTER_ROOT = re.compile(r"[A-Za-z]:\\", re.ASCII)

# Drive types no physical disk sits behind, so a failure to place one on a disk
# says nothing about any disk this tool lists.
DISKLESS_DRIVE_TYPES = frozenset({api.DRIVE_CDROM, api.DRIVE_RAMDISK})


class VolumeFacts(Protocol):
    """What decides whether a volume can be a filesystem row.

    A captured :class:`~.capture.VolumeEntry` carries these, and so does the
    reader's reading of a volume in progress, which is how one predicate
    answers for both.
    """

    @property
    def paths(self) -> Sequence[str]:
        """Every mount path the volume answers to."""
        ...

    @property
    def disks(self) -> Sequence[int]:
        """The disk numbers the volume's extents name."""
        ...

    @property
    def drive_type(self) -> int | None:
        """What ``GetDriveTypeW`` answered for its first path, ``None`` with no path."""
        ...


def can_be_a_row(volume: VolumeFacts, inventoried: Container[int]) -> bool:
    """Whether a volume becomes a filesystem row: the one rule the reader's walk and the builder share.

    A row needs a path to name it and an inventoried disk its extents reach;
    an optical drive or a RAM disk sits on no disk this tool lists whatever
    its extents say. The reader walks exactly these volumes' bitmaps, as
    Administrator, and the builder makes exactly these rows, so a figure is
    never read for a row that is then dropped, nor a row drawn whose figure
    was never asked for.

    Args:
        volume: What was read of the volume.
        inventoried: The disk numbers of the disks the inventory lists.

    Returns:
        Whether the volume is a row.
    """
    return (
        bool(volume.paths)
        and volume.drive_type not in DISKLESS_DRIVE_TYPES
        and any(number in inventoried for number in volume.disks)
    )


def named_first(paths: Sequence[str]) -> tuple[str, ...]:
    r"""A volume's paths with its drive-letter roots first, each kind in the order Windows listed it.

    ``GetVolumePathNamesForVolumeNameW`` documents no order, so a volume with
    a folder mount listed first was named by the folder. A stable sort moves
    the letters forward and reorders nothing else. The reader applies it
    before it asks anything of a path, so every call is asked of the letter
    the row is named by; the builder and :func:`resolve_usage` apply it again
    for a capture taken before the reader did.

    Args:
        paths: A volume's mount paths, in the order Windows listed them.

    Returns:
        The same paths, drive-letter roots first.

    Example:
        >>> named_first(("C:\\mnt\\c\\", "C:\\"))
        ('C:\\', 'C:\\mnt\\c\\')
    """
    return tuple(sorted(paths, key=lambda path: _DRIVE_LETTER_ROOT.fullmatch(path) is None))


def disk_numbers(nodes: frozenset[str]) -> dict[int, str]:
    """Map a disk's number back to its node name.

    Args:
        nodes: Every disk's node name.

    Returns:
        The disk number named in each node, keyed by that number. A node that
        does not carry a ``PhysicalDrive<n>`` number is not represented, since
        nothing a volume's extents name could ever match it.
    """
    numbered: dict[int, str] = {}
    for node in nodes:
        match = _NODE_NUMBER.search(node)
        if match:
            numbered[int(match.group(1))] = node
    return numbered


def _letter_mounts(volumes: dict[str, VolumeEntry], numbered: dict[int, str]) -> dict[str, list[str]]:
    """Every volume path reaching each disk, deduplicated and in first-seen order, letters first per volume.

    Args:
        volumes: Every volume the reader enumerated.
        numbered: A disk number's node name, as :func:`disk_numbers` maps it.

    Returns:
        Mount paths, keyed by the node of the disk they reach.
    """
    # A dict keeps first-seen order and looks a path up in constant time, where a
    # `not in` scan of a list made a disk with thousands of folder mounts quadratic.
    mounts: dict[str, dict[str, None]] = {}
    for entry in volumes.values():
        if not entry.paths:
            continue
        for disk_number in entry.disks:
            node = numbered.get(disk_number)
            if node is None:
                continue
            mounts.setdefault(node, {}).update(dict.fromkeys(named_first(entry.paths)))
    return {node: list(bucket) for node, bucket in mounts.items()}


def _boot_nodes(capture: WindowsCapture, volumes: dict[str, VolumeEntry], numbered: dict[int, str]) -> frozenset[str]:
    """Every disk node the machine boots from.

    Boot is the disk(s) of the Windows directory's own volume, plus the
    disk(s) of any volume whose partition is the EFI System Partition.

    Args:
        capture: A Windows reading.
        volumes: Every volume the reader enumerated.
        numbered: A disk number's node name, as :func:`disk_numbers` maps it.

    Returns:
        The node names of every boot disk.
    """
    boot_disk_numbers: set[int] = set()
    windows_volume = volumes.get(capture.windows_volume) if capture.windows_volume else None
    if windows_volume is not None:
        boot_disk_numbers.update(windows_volume.disks)
    for entry in volumes.values():
        if entry.esp:
            boot_disk_numbers.update(entry.disks)
    return frozenset(numbered[number] for number in boot_disk_numbers if number in numbered)


def blocks_not_mounted(entry: VolumeEntry, inventoried: Container[int]) -> bool:
    """Whether one volume's failure leaves a disk with nothing found undecided.

    A volume whose paths were not read could be any disk's letter - unless its
    extents were read and name no disk this inventory lists, when its letter
    belongs to a disk nobody is asked about. A volume
    that failed to open or to report its extents could sit on any disk, unless
    it is an optical drive or a RAM disk, or answers to no path at all: those
    are nothing a reader looks for under a letter, and a machine's every
    hidden recovery or reserved volume would otherwise blank the column. An
    EFI System Partition is the exception to "no path at all": it carries no
    letter by design, and a known ESP on an unknown disk could be any disk's
    boot mark. Only an ESP the reader actually READ as one counts - a volume
    that would not open carries no partition reading, and is not taken for one.

    Args:
        entry: One volume the reader enumerated.
        inventoried: The disk numbers of the disks the inventory lists.

    Returns:
        Whether "not mounted" can no longer be claimed for any disk.
    """
    if entry.paths_error is not None:
        return entry.error is not None or any(number in inventoried for number in entry.disks)
    if entry.error is None:
        return False
    if entry.esp is True:
        return True
    if not entry.paths:
        return False
    return entry.drive_type not in DISKLESS_DRIVE_TYPES


def resolve_usage(capture: WindowsCapture, environment: Environment) -> dict[str, DiskUsage | None]:
    """Resolve what every disk in a Windows capture is used for.

    Args:
        capture: A Windows reading, live or replayed.
        environment: What kind of machine the reading came from.

    Returns:
        One entry per disk, keyed by its node (``PhysicalDrive<n>``). Every
        value is ``None`` in a container and when the volumes section itself
        was never read. Among disks this capture COULD place a volume on, one
        with nothing resolved is ``None`` too whenever some other volume's
        failure could hide a use of it (see :func:`blocks_not_mounted`) - the
        global rule that "not mounted" is claimed only once every source that
        could have named the disk was read.
    """
    nodes = frozenset(entry.node for entry in capture.disks.values() if entry.node)
    if environment is Environment.CONTAINER or capture.volumes is None:
        return dict.fromkeys(nodes)

    volumes = capture.volumes
    numbered = disk_numbers(nodes)
    mounts = _letter_mounts(volumes, numbered)
    boot_nodes = _boot_nodes(capture, volumes, numbered)
    any_volume_blocks = any(blocks_not_mounted(entry, numbered) for entry in volumes.values())

    result: dict[str, DiskUsage | None] = {}
    for node in nodes:
        node_mounts = tuple(mounts.get(node, ()))
        boot = node in boot_nodes
        uses = (DiskUse(kind=UseKind.LETTER, mounts=node_mounts),) if node_mounts else ()
        if not uses and not boot and any_volume_blocks:
            result[node] = None
        else:
            result[node] = DiskUsage(boot=boot, uses=uses)
    return result


__all__ = [
    "DISKLESS_DRIVE_TYPES",
    "VolumeFacts",
    "blocks_not_mounted",
    "can_be_a_row",
    "disk_numbers",
    "named_first",
    "resolve_usage",
]
