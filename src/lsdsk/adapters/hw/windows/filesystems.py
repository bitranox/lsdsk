"""Turn a Windows reading into the volumes that live on its disks.

Pure: a capture in, one ``Filesystem`` per volume that has a path and sits on
an inventoried disk.

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
from .usage import blocks_not_mounted, disk_numbers

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
    """The byte figures of one volume, each present only when its source was read, none negative."""
    figures = _Figures()
    if entry.total_bytes is not None:
        figures["size_bytes"] = entry.total_bytes
        if entry.free_bytes is not None:
            figures["used_bytes"] = max(entry.total_bytes - entry.free_bytes, 0)
    if entry.available_bytes is not None:
        figures["free_bytes"] = entry.available_bytes
    return figures


def _volume_row(entry: VolumeEntry, disks: tuple[str, ...]) -> Filesystem:
    return Filesystem(
        kind=FilesystemKind.VOLUME,
        name=entry.paths[0],
        other_mounts=tuple(entry.paths[1:]),
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


def _unplaced_reason(entry: VolumeEntry) -> str:
    """Why one volume could not be placed, naming it where it has a name."""
    reasons = [reason for reason in (entry.paths_error, entry.error) if reason is not None]
    if entry.paths:
        return f"{entry.paths[0]}: " + "; ".join(reasons)
    if entry.esp is True:
        return "EFI system partition: " + "; ".join(reasons)
    return "; ".join(reasons)


def _unplaced_volumes(volumes: Iterable[VolumeEntry]) -> tuple[RefusedReading, ...]:
    """One refusal per volume that could be a row on an inventoried disk but could not be placed.

    Decided by :func:`.usage.blocks_not_mounted`, the rule that leaves the
    ``used by`` column undecided, so the two views cannot disagree about one
    volume: a volume that makes ``not mounted`` unclaimable is one this section
    may be missing a row for. An optical drive or a RAM disk, and a hidden
    volume with no path, are neither, and say nothing.
    """
    return tuple(
        RefusedReading(reading=_VOLUME_READING, reason=_unplaced_reason(entry))
        for entry in volumes
        if blocks_not_mounted(entry)
    )


def build_filesystems(
    capture: WindowsCapture, environment: Environment
) -> tuple[tuple[Filesystem, ...] | None, tuple[RefusedReading, ...]]:
    """Every volume with a path that lives on one of the capture's disks.

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
    placed = [(entry, _nodes_of(entry, numbered)) for entry in capture.volumes.values() if entry.paths]
    rows = [_volume_row(entry, nodes) for entry, nodes in placed if nodes]
    ordered = tuple(sorted(rows, key=lambda row: disk_name_order(row.disks[0])))
    unplaced = _unplaced_volumes(capture.volumes.values())
    if rows and capture.volume_bitmaps_read is False:
        return ordered, (*unplaced, *refusals_of({"fragmentation": _NEEDS_ADMINISTRATOR}))
    return ordered, unplaced


__all__ = ["build_filesystems"]
