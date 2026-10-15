"""Turn a Windows reading into the volumes that live on its disks.

Pure: a capture in, one ``Filesystem`` per volume that :func:`.usage.can_be_a_row`
admits: a path, an inventoried disk, and not an optical drive or a RAM disk.

System Role:
    Adapter layer, translation half of the Windows mapping path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from ....domain.disk_name import disk_name_order
from ....domain.enums import Environment, FilesystemKind
from ....domain.models import Filesystem, RefusedReading
from ..free_space import free_space_of
from ..refusals import refusals_of
from .usage import blocks_not_mounted, can_be_a_row, disk_numbers, named_first

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .capture import VolumeEntry, WindowsCapture

#: Why a run that could not open a volume for reading has no fragmentation figure.
_NEEDS_ADMINISTRATOR = "needs Administrator"
#: The reading a volume that could not be placed on a disk is refused under.
_VOLUME_READING = "volume"


class _Figures(TypedDict, total=False):
    """The measured fields of a ``Filesystem``, present only where read."""

    size_bytes: int
    used_bytes: int
    free_bytes: int


def _figures(entry: VolumeEntry) -> _Figures:
    """The byte figures of one volume, each present only when its source was read, none negative.

    All three are the CALLER's, the one scope ``GetDiskFreeSpaceExW`` answers
    for both a total and a free figure: under a per-user quota its total and
    its free-to-the-caller may be less than the volume's, while its
    total-free is always the volume's (Microsoft Learn, GetDiskFreeSpaceExW,
    on ``lpTotalNumberOfBytes``, ``lpFreeBytesAvailableToCaller`` and
    ``lpTotalNumberOfFreeBytes``). Used is therefore the caller's total less
    the caller's free; subtracting the volume's free from a quota's total read
    a quota-limited user as using nothing. With no quota the two free figures
    are the same number and the row is the volume's own.
    """
    figures = _Figures()
    if entry.total_bytes is not None:
        figures["size_bytes"] = entry.total_bytes
        if entry.available_bytes is not None:
            figures["used_bytes"] = max(entry.total_bytes - entry.available_bytes, 0)
    if entry.available_bytes is not None:
        figures["free_bytes"] = entry.available_bytes
    return figures


def _volume_row(entry: VolumeEntry, disks: tuple[str, ...]) -> Filesystem:
    paths = named_first(entry.paths)
    return Filesystem(
        kind=FilesystemKind.VOLUME,
        name=paths[0],
        other_mounts=paths[1:],
        fs_type=entry.fs_type or "",
        disks=disks,
        read_only=entry.read_only is True,
        free_space=free_space_of(entry.free_extents),
        readings_refused=refusals_of(
            {
                "volume-space": entry.space_error,
                "volume-info": entry.fs_info_error,
                "volume-bitmap": None if entry.free_extents is None else entry.free_extents.error,
            }
        ),
        **_figures(entry),
    )


def _nodes_of(entry: VolumeEntry, numbered: dict[int, str]) -> tuple[str, ...]:
    """The inventory disks a volume's extents reach, in disk order."""
    nodes = {numbered[number] for number in entry.disks if number in numbered}
    return tuple(sorted(nodes, key=disk_name_order))


def _space_was_read(volumes: Iterable[VolumeEntry]) -> bool:
    """Whether any volume with a path carries what the space reading writes.

    The reader asks every volume that has a path and always records either the
    figures or the reason they were refused, from each of its two calls; a
    capture taken before those fields existed carries none of them on any
    volume, and its volumes are then not read rather than read as blank.
    """
    return any(
        entry.total_bytes is not None
        or entry.space_error is not None
        or entry.fs_type is not None
        or entry.fs_info_error is not None
        for entry in volumes
        if entry.paths
    )


def _unplaced_name(position: int, entry: VolumeEntry, numbered: dict[int, str]) -> str:
    """What a reader knows an unplaced volume by: its drive letter, or its place and the disks it reached.

    A volume whose paths were not read has no name of its own, so it is named
    by its place in the enumeration, which no two volumes share, then by the
    disks its extents reached, an inventoried one by its node and any other by
    its number. Without the place, two such volumes on one disk printed one
    line twice.

    Args:
        position: The volume's place among the capture's volumes, from 0.
        entry: The volume.
        numbered: An inventoried disk's node, keyed by its number.

    Returns:
        The name the refusal line starts with.
    """
    if entry.paths:
        return named_first(entry.paths)[0]
    name = f"volume {position}"
    disks = [numbered.get(number, f"disk {number}") for number in sorted(set(entry.disks))]
    if disks:
        name += " on " + ", ".join(disks)
    if entry.esp is True:
        name += " (EFI system partition)"
    return name


def _unplaced_reason(position: int, entry: VolumeEntry, numbered: dict[int, str]) -> str:
    """Why one volume could not be placed, under the name :func:`_unplaced_name` gives it."""
    reasons = [reason for reason in (entry.paths_error, entry.error) if reason is not None]
    return f"{_unplaced_name(position, entry, numbered)}: " + "; ".join(reasons)


def _may_have_a_path(entry: VolumeEntry) -> bool:
    """Whether a volume has a path, or might: its paths were read and found, or were never read."""
    return bool(entry.paths) or entry.paths_error is not None


def _unplaced_volumes(volumes: Iterable[VolumeEntry], numbered: dict[int, str]) -> tuple[RefusedReading, ...]:
    """One refusal per volume that could be a row on an inventoried disk but could not be placed.

    Decided first by :func:`.usage.blocks_not_mounted`, the rule that leaves
    the ``used by`` column undecided, so this section never refuses a volume
    the column could decide: an optical drive or a RAM disk says nothing. A
    row also needs a path, so a volume known to have none is silent here too.
    That is where the two views part: an EFI system partition carries no path
    by design, so its failure leaves no row missing, while it still leaves the
    column undecided, a known ESP on an unknown disk being any disk's boot mark.
    """
    return tuple(
        RefusedReading(reading=_VOLUME_READING, reason=_unplaced_reason(position, entry, numbered))
        for position, entry in enumerate(volumes)
        if blocks_not_mounted(entry) and _may_have_a_path(entry)
    )


def build_filesystems(
    capture: WindowsCapture, environment: Environment
) -> tuple[tuple[Filesystem, ...] | None, tuple[RefusedReading, ...]]:
    """Every volume that can be a row: a path, one of the capture's disks, and a drive type a disk sits behind.

    Args:
        capture: A Windows reading, live or replayed.
        environment: What kind of machine it came from.

    Returns:
        The rows and the refusals that belong to no single row: one per
        volume that could not be placed on a disk (see
        :func:`_unplaced_volumes`), and one when an unelevated run could not
        read the volumes' bitmaps. The rows are ``None`` in a container or when the
        volumes were not read, the same rule as ``used by``, and ``None`` too
        for a capture taken before the volumes' space was read.
    """
    if environment is Environment.CONTAINER or capture.volumes is None:
        return None, ()
    if not _space_was_read(capture.volumes.values()):
        return None, ()
    numbered = disk_numbers(frozenset(entry.node for entry in capture.disks.values() if entry.node))
    rows = [
        _volume_row(entry, _nodes_of(entry, numbered))
        for entry in capture.volumes.values()
        if can_be_a_row(entry, numbered)
    ]
    ordered = tuple(sorted(rows, key=lambda row: disk_name_order(row.disks[0])))
    unplaced = _unplaced_volumes(capture.volumes.values(), numbered)
    if rows and capture.volume_bitmaps_read is False:
        return ordered, (*unplaced, *refusals_of({"fragmentation": _NEEDS_ADMINISTRATOR}))
    return ordered, unplaced


__all__ = ["build_filesystems"]
