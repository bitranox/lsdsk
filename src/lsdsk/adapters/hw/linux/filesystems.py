"""Turn a Linux reading into the filesystems that live on its disks.

Pure: a capture in, the rows and the refusals that belong to no single row out.

System Role:
    Adapter layer, translation half of the Linux mapping path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from ....domain.disk_name import disk_name_order
from ....domain.enums import Environment, FilesystemKind
from ....domain.models import FIXED_INODE_TABLE_TYPES, Filesystem, RefusedReading
from ..refusals import refusals_of
from .usage import disks_by_device, disks_by_pool

if TYPE_CHECKING:
    from .capture import LinuxCapture, MountEntry, SpaceEntry


class _Figures(TypedDict, total=False):
    """The measured fields of a ``Filesystem``, present only where read."""

    size_bytes: int
    used_bytes: int
    free_bytes: int
    inodes_total: int | None
    inodes_free: int | None


_ZFS = "zfs"
_NO_CONTROL_DEVICE = "the ZFS control device was not available"


def _grouped(mounts: tuple[MountEntry, ...]) -> dict[str, list[MountEntry]]:
    """Every non-ZFS mount, grouped by the filesystem's device number in first-seen order."""
    groups: dict[str, list[MountEntry]] = {}
    for mount in mounts:
        if mount.fstype != _ZFS:
            groups.setdefault(mount.dev, []).append(mount)
    return groups


def _inode_figures(space: SpaceEntry) -> _Figures:
    """The inode counts, only when both were read, with the free count never above the total."""
    if space.files is None or space.ffree is None:
        return {}
    return {"inodes_total": space.files, "inodes_free": min(space.ffree, space.files)}


def _figures(space: SpaceEntry | None, fs_type: str) -> _Figures:
    """The byte and inode figures of one statvfs reading, none of them negative."""
    if space is None or space.error is not None or space.frsize is None:
        return {}
    blocks, bfree, bavail = space.blocks or 0, space.bfree or 0, space.bavail or 0
    figures = _Figures(
        size_bytes=blocks * space.frsize,
        used_bytes=max(blocks - bfree, 0) * space.frsize,
        free_bytes=bavail * space.frsize,
    )
    if fs_type in FIXED_INODE_TABLE_TYPES:
        figures.update(_inode_figures(space))
    return figures


def _mount_row(group: list[MountEntry], disks: frozenset[str], space: SpaceEntry | None) -> Filesystem:
    first = group[0]
    mountpoints = list(dict.fromkeys(mount.mountpoint for mount in group))
    return Filesystem(
        kind=FilesystemKind.MOUNT,
        name=mountpoints[0],
        other_mounts=tuple(mountpoints[1:]),
        fs_type=first.fstype,
        disks=tuple(sorted(disks, key=disk_name_order)),
        read_only=all(mount.read_only is True for mount in group),
        readings_refused=refusals_of({"statvfs": None if space is None else space.error}),
        **_figures(space, first.fstype),
    )


def _mount_rows(capture: LinuxCapture) -> list[Filesystem]:
    by_device = disks_by_device(capture)
    space = capture.filesystem_space or {}
    rows: list[Filesystem] = []
    for dev, group in _grouped(tuple(capture.mounts or ())).items():
        disks = by_device.get(dev, frozenset()) | by_device.get(group[0].source_dev or "", frozenset())
        if disks:
            rows.append(_mount_row(group, disks, space.get(dev)))
    return rows


def _pool_rows(capture: LinuxCapture) -> list[Filesystem]:
    members = disks_by_pool(capture)
    pools = capture.zfs_pools or {}
    rows: list[Filesystem] = []
    for name in sorted(members):
        entry = pools.get(name)
        figures = (
            _Figures()
            if entry is None
            else _Figures(
                size_bytes=entry.size,
                used_bytes=min(entry.alloc, entry.size),
                free_bytes=max(entry.size - entry.alloc, 0),
            )
        )
        rows.append(
            Filesystem(
                kind=FilesystemKind.POOL,
                name=name,
                fs_type=_ZFS,
                disks=tuple(sorted(members[name], key=disk_name_order)),
                **figures,
            )
        )
    return rows


def _pool_refusals(capture: LinuxCapture) -> tuple[RefusedReading, ...]:
    if capture.zfs_pools_error is not None:
        return refusals_of({"zfs-pools": capture.zfs_pools_error})
    if capture.zfs_pools is None and disks_by_pool(capture):
        return refusals_of({"zfs-pools": _NO_CONTROL_DEVICE})
    return ()


def build_filesystems(
    capture: LinuxCapture, environment: Environment
) -> tuple[tuple[Filesystem, ...] | None, tuple[RefusedReading, ...]]:
    """Every filesystem and pool that lives on one of the capture's disks.

    Args:
        capture: A Linux reading, live or replayed.
        environment: What kind of machine it came from.

    Returns:
        The rows (``None`` in a container or when mountinfo was not read, the
        same rule as ``used by``) and the refusals that belong to no single row.
    """
    if environment is Environment.CONTAINER or capture.mounts is None:
        return None, ()
    return (*_mount_rows(capture), *_pool_rows(capture)), _pool_refusals(capture)


__all__ = ["build_filesystems"]
