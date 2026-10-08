"""Resolve what uses each Windows disk, from what the reader recorded.

Pure: a capture in, one ``DiskUsage`` per disk out.

System Role:
    Adapter layer, translation half of the Windows mapping path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ....domain.enums import Environment, UseKind
from ....domain.models import DiskUsage, DiskUse
from . import winapi as api

if TYPE_CHECKING:
    from .capture import VolumeEntry, WindowsCapture

#: A disk's node, as :mod:`.builder` names it. A fixture capture's
#: ``DiskEntry.node`` is always already in this shape (confirmed against
#: the committed Windows fixtures), so this reads the number back out rather
#: than re-deriving the name from the interface path the way the builder does.
_NODE_NUMBER = re.compile(r"PhysicalDrive(\d+)$", re.IGNORECASE)

# Drive types no physical disk sits behind, so a failure to place one on a disk
# says nothing about any disk this tool lists.
_DISKLESS_DRIVE_TYPES = frozenset({api.DRIVE_CDROM, api.DRIVE_RAMDISK})


def _disk_numbers(nodes: frozenset[str]) -> dict[int, str]:
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


def _letter_mounts(volumes: dict[str, VolumeEntry], disk_numbers: dict[int, str]) -> dict[str, list[str]]:
    """Every volume path reaching each disk, deduplicated and in first-seen order.

    Args:
        volumes: Every volume the reader enumerated.
        disk_numbers: A disk number's node name.

    Returns:
        Mount paths, keyed by the node of the disk they reach.
    """
    mounts: dict[str, list[str]] = {}
    for entry in volumes.values():
        if not entry.paths:
            continue
        for disk_number in entry.disks:
            node = disk_numbers.get(disk_number)
            if node is None:
                continue
            bucket = mounts.setdefault(node, [])
            for path in entry.paths:
                if path not in bucket:
                    bucket.append(path)
    return mounts


def _boot_nodes(
    capture: WindowsCapture, volumes: dict[str, VolumeEntry], disk_numbers: dict[int, str]
) -> frozenset[str]:
    """Every disk node the machine boots from.

    Boot is the disk(s) of the Windows directory's own volume, plus the
    disk(s) of any volume whose partition is the EFI System Partition.

    Args:
        capture: A Windows reading.
        volumes: Every volume the reader enumerated.
        disk_numbers: A disk number's node name.

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
    return frozenset(disk_numbers[number] for number in boot_disk_numbers if number in disk_numbers)


def _blocks_not_mounted(entry: VolumeEntry) -> bool:
    """Whether one volume's failure leaves a disk with nothing found undecided.

    A volume whose paths were not read could be any disk's letter. A volume
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

    Returns:
        Whether "not mounted" can no longer be claimed for any disk.
    """
    if entry.paths_error is not None:
        return True
    if entry.error is None:
        return False
    if entry.esp is True:
        return True
    if not entry.paths:
        return False
    return entry.drive_type not in _DISKLESS_DRIVE_TYPES


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
        failure could hide a use of it (see :func:`_blocks_not_mounted`) - the
        global rule that "not mounted" is claimed only once every source that
        could have named the disk was read.
    """
    nodes = frozenset(entry.node for entry in capture.disks.values() if entry.node)
    if environment is Environment.CONTAINER or capture.volumes is None:
        return dict.fromkeys(nodes)

    volumes = capture.volumes
    disk_numbers = _disk_numbers(nodes)
    mounts = _letter_mounts(volumes, disk_numbers)
    boot_nodes = _boot_nodes(capture, volumes, disk_numbers)
    any_volume_blocks = any(_blocks_not_mounted(entry) for entry in volumes.values())

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


__all__ = ["resolve_usage"]
