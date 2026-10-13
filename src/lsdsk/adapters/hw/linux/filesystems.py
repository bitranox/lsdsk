"""Turn a Linux reading into the filesystems that live on its disks.

Pure: a capture in, the rows and the refusals that belong to no single row out.

System Role:
    Adapter layer, translation half of the Linux mapping path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from ....domain.disk_name import disk_name_order
from ....domain.enums import Environment, FilesystemKind
from ....domain.models import FIXED_INODE_TABLE_TYPES, Filesystem, FreeSpace, RefusedReading
from ..free_space import free_space_of
from ..refusals import refusals_of
from .usage import disks_by_device, disks_by_pool

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..capture import FreeExtentsEntry
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


def _mount_row(
    group: list[MountEntry], disks: frozenset[str], space: SpaceEntry | None, *, extents: FreeExtentsEntry | None
) -> Filesystem:
    first = group[0]
    # A reachable mountpoint names the row: it is where the figures were read,
    # and a covered one is the very name the mount on top of it carries.
    reachable_first = [mount for mount in group if not mount.hidden] + [mount for mount in group if mount.hidden]
    mountpoints = list(dict.fromkeys(mount.mountpoint for mount in reachable_first))
    return Filesystem(
        kind=FilesystemKind.MOUNT,
        name=mountpoints[0],
        other_mounts=tuple(mountpoints[1:]),
        fs_type=first.fstype,
        disks=tuple(sorted(disks, key=disk_name_order)),
        read_only=all(mount.read_only is True for mount in group),
        free_space=free_space_of(extents),
        readings_refused=refusals_of(
            {
                "statvfs": None if space is None else space.error,
                "free-extents": None if extents is None else extents.error,
            }
        ),
        **_figures(space, first.fstype),
    )


def _mount_rows(
    capture: LinuxCapture, *, mounts: Sequence[MountEntry], space: Mapping[str, SpaceEntry]
) -> list[Filesystem]:
    by_device = disks_by_device(capture)
    rows: list[Filesystem] = []
    for dev, group in _grouped(tuple(mounts)).items():
        disks = by_device.get(dev, frozenset()) | by_device.get(group[0].source_dev or "", frozenset())
        if disks:
            extents = None if capture.free_extents is None else capture.free_extents.get(dev)
            rows.append(_mount_row(group, disks, space.get(dev), extents=extents))
    return rows


def _pool_free_space(fragmentation: Mapping[str, int | None] | None, name: str) -> FreeSpace | None:
    """A pool's FRAG as its free-space value; ``None`` where it was not read."""
    if fragmentation is None or name not in fragmentation:
        return None
    return FreeSpace(fragmentation_percent=fragmentation[name])


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
                free_space=_pool_free_space(capture.zfs_fragmentation, name),
                **figures,
            )
        )
    return rows


def _pool_refusals(capture: LinuxCapture) -> tuple[RefusedReading, ...]:
    """The pool listing's refusal, reported only when a disk here carries a pool member.

    A pool row exists only for a pool with a member on an inventory disk, so a
    listing that failed - refused, or with no control device at all - can have
    missed a row only when such a member exists; anywhere else it missed nothing.
    """
    if not disks_by_pool(capture):
        return ()
    if capture.zfs_pools_error is not None:
        return refusals_of({"zfs-pools": capture.zfs_pools_error})
    if capture.zfs_pools is None:
        return refusals_of({"zfs-pools": _NO_CONTROL_DEVICE})
    return refusals_of({"zfs-fragmentation": capture.zfs_fragmentation_error})


def build_filesystems(
    capture: LinuxCapture, environment: Environment
) -> tuple[tuple[Filesystem, ...] | None, tuple[RefusedReading, ...]]:
    """Every filesystem and pool that lives on one of the capture's disks.

    Args:
        capture: A Linux reading, live or replayed.
        environment: What kind of machine it came from.

    Returns:
        The rows and the refusals that belong to no single row. The rows are
        ``None`` in a container or when mountinfo was not read, the same rule
        as ``used by``, and ``None`` too for a capture taken before space was
        read at all: the live reader writes ``filesystem_space`` whenever it
        read mountinfo, so its absence marks such a capture, and reading it as
        an empty reading would draw a row of ``-`` for every mount and pool.
    """
    mounts, space = capture.mounts, capture.filesystem_space
    if environment is Environment.CONTAINER or mounts is None or space is None:
        return None, ()
    return (*_mount_rows(capture, mounts=mounts, space=space), *_pool_rows(capture)), _pool_refusals(capture)


__all__ = ["build_filesystems"]
