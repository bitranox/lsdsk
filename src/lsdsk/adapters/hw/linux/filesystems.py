"""Turn a Linux reading into the filesystems that live on its disks.

Pure: a capture in, the rows and the refusals that belong to no single row out.

A capture that carries ``zfs_pool_layouts`` names each listed pool's leaf
vdevs by device number, read from the pool's own config, so a pool's member is
the device the kernel's pool names and nothing else. A udev label decides only
where the config could not: a member whose path did not resolve, or resolved
to a device udev reads as something else, is looked for by its pool's GUID,
never by its name, because a guest's root pool on a host volume carries a
label of the host pool's name. A capture taken before that
field joins pools to disks by their members' labels alone, as it always did.

System Role:
    Adapter layer, translation half of the Linux mapping path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, TypedDict

from ....domain.disk_name import disk_name_order
from ....domain.enums import Environment, FilesystemKind
from ....domain.models import FIXED_INODE_TABLE_TYPES, Filesystem, FreeSpace, RefusedReading
from ..free_space import free_space_of
from ..refusals import refusals_of
from .usage import disk_placement, disks_by_pool, pool_disks, pool_lookup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..capture import FreeExtentsEntry
    from .capture import LinuxCapture, MountEntry, PoolLayout, SpaceEntry


class _Figures(TypedDict, total=False):
    """The measured fields of a ``Filesystem``, present only where read."""

    size_bytes: int
    used_bytes: int
    free_bytes: int
    inodes_total: int | None
    inodes_free: int | None


_ZFS = "zfs"
_NO_CONTROL_DEVICE = "the ZFS control device was not available"
# A pool the listing names that no member joins to an inventoried disk, where
# a reading not taken could have: its fill cannot be placed on a row, so it is
# said rather than dropped.
_UNTRACED_POOL = "zfs-pool-disks"
_UNTRACED_POOL_REASON = "no member was traced to a disk this scan inventoried"


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
    placement = disk_placement(capture)
    rows: list[Filesystem] = []
    for dev, group in _grouped(tuple(mounts)).items():
        # The live reader stats and walks a filesystem by this same placement, so it skips no row drawn here.
        disks = placement.disks_of(dev, (mount.source_dev for mount in group))
        if disks:
            extents = None if capture.free_extents is None else capture.free_extents.get(dev)
            rows.append(_mount_row(group, disks, space.get(dev), extents=extents))
    return rows


class _PoolTrace(NamedTuple):
    """Which disks each pool sits on, and which listed pools a reading that was not taken could have placed.

    Attributes:
        disks: Pool name to the disk nodes carrying its members; a pool here is a row.
        untraced: Every listed pool with no row that something unread could
            have placed, in name order: a member on a device nothing in the
            capture reaches, a path that did not resolve, a config with no vdev
            tree. A pool whose members are all files or virtual devices is
            not here, because there was nothing to trace.
        members_on_disks: Whether an inventoried disk is known to carry a pool
            member, which is what lets a refused pool listing have missed a row.
    """

    disks: dict[str, frozenset[str]]
    untraced: tuple[str, ...]
    members_on_disks: bool


def _traced_from_layouts(capture: LinuxCapture, layouts: Mapping[str, PoolLayout]) -> _PoolTrace:
    lookup = pool_lookup(capture)
    rows: dict[str, frozenset[str]] = {}
    untraced: list[str] = []
    listed: set[str] = {*(capture.zfs_pools or {}), *layouts}
    for name in sorted(listed):
        disks, unfollowed = pool_disks(layouts.get(name), lookup)
        if disks:
            rows[name] = disks
        elif unfollowed:
            untraced.append(name)
    # With no listing at all only a refused one could have missed a pool, and only a member label can say so.
    listing_refused = capture.zfs_pools is None and capture.zfs_pools_error is not None
    return _PoolTrace(rows, tuple(untraced), bool(rows) or (listing_refused and bool(disks_by_pool(capture))))


def _imported_by_label(capture: LinuxCapture) -> dict[str, frozenset[str]]:
    """Every pool a member label names, less those a complete pool listing shows are not imported.

    ZFS keeps its labels at the end of a disk, so a reused disk goes on
    naming a pool long after it was destroyed or exported. A listing read
    without a refusal holds every imported pool, so a name it lacks is no pool
    of this machine; where it was not read, or could not read some pool, the
    label may still be one, and keeps its unread row.
    """
    members = disks_by_pool(capture)
    if capture.zfs_pools is None or capture.zfs_pools_error is not None:
        return members
    return {name: disks for name, disks in members.items() if name in capture.zfs_pools}


def _traced_from_labels(capture: LinuxCapture) -> _PoolTrace:
    members = _imported_by_label(capture)
    untraced = tuple(name for name in sorted(capture.zfs_pools or {}) if name not in members)
    return _PoolTrace(members, untraced, bool(members))


def _trace_pools(capture: LinuxCapture) -> _PoolTrace:
    """Which disks each ZFS pool sits on, and which listed pools could not be placed.

    Args:
        capture: A Linux reading, live or replayed.

    Returns:
        The pools with a row, the listed pools a refusal names, and whether a
        member is known to sit on an inventoried disk. With pool layouts a row
        exists only for a pool the kernel listed; without them, for every pool
        a member label names that a complete listing does not rule out.
    """
    if capture.zfs_pool_layouts is None:
        return _traced_from_labels(capture)
    return _traced_from_layouts(capture, capture.zfs_pool_layouts)


def _pool_free_space(fragmentation: Mapping[str, int | None] | None, name: str) -> FreeSpace | None:
    """A pool's FRAG as its free-space value; ``None`` where it was not read."""
    if fragmentation is None or name not in fragmentation:
        return None
    return FreeSpace(fragmentation_percent=fragmentation[name])


def _pool_rows(capture: LinuxCapture, members: Mapping[str, frozenset[str]]) -> list[Filesystem]:
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


def _untraced_pools(trace: _PoolTrace) -> tuple[RefusedReading, ...]:
    """One refusal per listed pool with no row that a reading not taken could have placed.

    Such a pool has no row, so without this its fill would go unjudged in
    silence: a pool whose members sit behind a layer the reader did not follow
    reads exactly like no pool at all. A pool whose members are all files or
    virtual devices is not one: there was no disk of its own to find.
    """
    return tuple(
        RefusedReading(reading=_UNTRACED_POOL, reason=f"pool {name}: {_UNTRACED_POOL_REASON}")
        for name in trace.untraced
    )


def _pool_refusals(capture: LinuxCapture, trace: _PoolTrace) -> tuple[RefusedReading, ...]:
    """The pool listing's and fragmentation reading's refusals, then every listed pool no disk was traced to.

    A pool row exists only for a pool with a member on an inventory disk, so a
    listing that failed can have missed a row only when such a member exists;
    anywhere else it missed nothing. A listed pool with no such member is the
    opposite case: the listing read it and no row can carry it.
    """
    untraced = _untraced_pools(trace)
    if not trace.members_on_disks:
        return untraced
    listing = capture.zfs_pools_error
    if listing is None and capture.zfs_pools is None:
        listing = _NO_CONTROL_DEVICE
    return (*refusals_of({"zfs-pools": listing, "zfs-fragmentation": capture.zfs_fragmentation_error}), *untraced)


def builds_filesystems(environment: Environment) -> bool:
    """Whether a machine of this kind gets a filesystems section at all.

    Not in a container, where the host's own use of its disks is invisible -
    the same rule as ``used by``. The live reader asks it too, so it reads no
    filesystem figure for a section that will not be drawn.

    Args:
        environment: What kind of machine the reading came from.

    Returns:
        ``False`` in a container, ``True`` anywhere else.

    Example:
        >>> builds_filesystems(Environment.CONTAINER), builds_filesystems(Environment.BARE_METAL)
        (False, True)
    """
    return environment is not Environment.CONTAINER


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
    if not builds_filesystems(environment) or mounts is None or space is None:
        return None, ()
    trace = _trace_pools(capture)
    rows = (*_mount_rows(capture, mounts=mounts, space=space), *_pool_rows(capture, trace.disks))
    return rows, _pool_refusals(capture, trace)


__all__ = ["build_filesystems", "builds_filesystems"]
