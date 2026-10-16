"""Resolve what uses each Linux disk, from what the reader recorded.

Pure: a capture in, one ``DiskUsage`` per disk out.

System Role:
    Adapter layer, translation half of the Linux mapping path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from ....domain.enums import Environment, UseKind
from ....domain.models import DiskUsage, DiskUse
from .capture import BlockEntry, FilesystemSignature, LinuxCapture, StackedEntry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

#: Mountpoints whose disk the machine boots from. ``/efi`` is systemd's
#: recommended mountpoint for the EFI system partition, the same intent as
#: ``/boot/efi``.
BOOT_MOUNTS = frozenset({"/", "/boot", "/boot/efi", "/efi"})

_ZFS_MEMBER = "zfs_member"

# Filesystems that span several devices while mountinfo names only one of them
# as the mount's source. The other members cannot be joined to the mount from
# what the reader records, so one that resolved nothing is undecided rather
# than unused.
_UNJOINABLE_MEMBER_TYPES = frozenset({"btrfs"})

# LVM writes a dash inside a group or volume name as two, so the single dash is
# the separator between them.
_LVM_SEPARATOR = re.compile(r"(?<!-)-(?!-)")


class _Sources(NamedTuple):
    """The capture's cross-referencing tables.

    Built once rather than threaded as three separate parameters through
    every helper below.

    Attributes:
        mounts_by_dev: Mountpoints, deduplicated and in order, keyed by every
            ``maj:min`` that names them (a mount's own device and, for a
            filesystem like btrfs whose mountinfo device is synthetic, the
            resolved source device too).
        swap_devs: Every device active swap runs on.
        boot_pools: ZFS pool names the machine boots from.
    """

    mounts_by_dev: Mapping[str, tuple[str, ...]]
    swap_devs: frozenset[str]
    boot_pools: frozenset[str]


def _mounts_by_dev(capture: LinuxCapture) -> dict[str, tuple[str, ...]]:
    """Mountpoints, deduplicated and in first-seen order, keyed by device.

    Args:
        capture: A Linux reading.

    Returns:
        Mountpoints keyed by both a mount's own ``dev`` and its resolved
        ``source_dev``, when it has one.
    """
    # A dict per device is the ordered set: membership is a hash lookup, where
    # `not in` on a list made thousands of bind mounts of one device quadratic.
    ordered: dict[str, dict[str, None]] = {}
    for mount in capture.mounts or ():
        for dev in (mount.dev, mount.source_dev):
            if dev:
                ordered.setdefault(dev, {})[mount.mountpoint] = None
    return {dev: tuple(mountpoints) for dev, mountpoints in ordered.items()}


def _swap_devs(capture: LinuxCapture) -> frozenset[str]:
    """Every device that is active swap.

    Args:
        capture: A Linux reading.

    Returns:
        The ``maj:min`` of every swap that resolved to a device node; empty
        when the swap list was not read.
    """
    return frozenset(swap.dev for swap in capture.swaps or () if swap.dev)


def _boot_pools(capture: LinuxCapture) -> frozenset[str]:
    """ZFS pool names the machine boots from.

    Args:
        capture: A Linux reading.

    Returns:
        The pool name of every ``zfs`` mount under a boot mountpoint, read
        from the dataset name mountinfo's source field carries.
    """
    return frozenset(
        mount.source.split("/")[0]
        for mount in capture.mounts or ()
        if mount.mountpoint in BOOT_MOUNTS and mount.fstype == "zfs" and mount.source
    )


def _holders_of(entry: StackedEntry) -> list[str]:
    """What sits on a stacked device, directly or on one of its own partitions.

    Args:
        entry: A stacked device's reading.

    Returns:
        The device's own holders, then each of its partitions' holders.
        Nothing from ``partitions`` when they were never read.
    """
    return [
        *entry.holders,
        *(holder for partition in (entry.partitions or {}).values() for holder in partition.holders),
    ]


def _devs_of(entry: StackedEntry) -> list[str]:
    """Every ``maj:min`` a mount or a swap could name for a stacked device.

    A partitioned md array (IMSM or DDF fake RAID) is mounted through its own
    partitions, which are not its holders, so they are listed here beside the
    device itself.

    Args:
        entry: A stacked device's reading.

    Returns:
        The device's own number, then each of its partitions', where read.
        Nothing from ``partitions`` when they were never read.
    """
    candidates = (entry.dev, *(partition.dev for partition in (entry.partitions or {}).values()))
    return [dev for dev in candidates if dev]


def _closure(name: str, stacked: Mapping[str, StackedEntry]) -> list[str]:
    """Every device-mapper device transitively stacked on top of ``name``.

    Args:
        name: A stacked device's kernel name.
        stacked: Every stacked device, keyed by kernel name.

    Returns:
        The names reached below ``name``, cycle-safe, in discovery order.
        ``name`` itself is never included.
    """
    visited = {name}
    closure: list[str] = []
    queue = _holders_of(stacked.get(name, StackedEntry()))
    while queue:
        candidate = queue.pop(0)
        if candidate in visited:
            continue
        visited.add(candidate)
        closure.append(candidate)
        entry = stacked.get(candidate)
        if entry is not None:
            queue.extend(_holders_of(entry))
    return closure


def _lvm_group(dm_name: str) -> str:
    """The volume group's own name, out of a device-mapper mapping name.

    Args:
        dm_name: The mapping name LVM publishes, such as ``vg0-var`` or an
            escaped ``my--vg-lv``.

    Returns:
        The group name, with LVM's doubled-dash escaping undone.
    """
    group = _LVM_SEPARATOR.split(dm_name, maxsplit=1)[0]
    return group.replace("--", "-")


def _stack_kind(name: str, dm_uuid: str | None, dm_name: str | None) -> tuple[UseKind, str]:
    """What kind of stacked device ``name`` is, and what it should be named.

    Args:
        name: The stacked device's kernel name.
        dm_uuid: Its mapping UUID, when it publishes one.
        dm_name: Its mapping name, when it publishes one.

    Returns:
        The use kind and the name it should be reported under.
    """
    if dm_uuid is not None and dm_uuid.startswith("LVM-"):
        return UseKind.LVM, _lvm_group(dm_name) if dm_name else name
    if dm_uuid is not None and dm_uuid.startswith("CRYPT-"):
        return UseKind.CRYPT, dm_name or name
    if dm_uuid is None and name.startswith("md"):
        return UseKind.MD, name
    return UseKind.STACK, dm_name or name


def _stack_use(name: str, stacked: Mapping[str, StackedEntry], sources: _Sources) -> DiskUse:
    """The one use a disk's holder (a device-mapper device) represents.

    Reports the holder's own layer, with every mount reached below it through
    the stack (LVM under crypt, a filesystem on an LVM volume), so a chain is
    read one layer at a time from the disk's side rather than disappearing
    into its deepest mount.

    Args:
        name: The holder's kernel name, as recorded on a disk or partition.
        stacked: Every stacked device, keyed by kernel name.
        sources: The capture's cross-referencing tables.

    Returns:
        One use, named for the mapping's own identity.
    """
    entry = stacked.get(name)
    dm_uuid = entry.dm_uuid if entry is not None else None
    dm_name = entry.dm_name if entry is not None else None
    kind, group = _stack_kind(name, dm_uuid, dm_name)

    members = [name, *_closure(name, stacked)]
    mounts: dict[str, None] = {}
    is_swap = False
    devs = [dev for member in members for dev in _devs_of(stacked.get(member, StackedEntry()))]
    for dev in devs:
        mounts.update(dict.fromkeys(sources.mounts_by_dev.get(dev, ())))
        is_swap = is_swap or dev in sources.swap_devs
    if is_swap:
        mounts.setdefault("swap")
    return DiskUse(kind=kind, name=group, mounts=tuple(mounts))


def _leaf_uses(
    dev: str | None,
    holders: tuple[str, ...],
    signature: FilesystemSignature | None,
    sources: _Sources,
    stacked: Mapping[str, StackedEntry],
) -> list[DiskUse]:
    """Everything found directly on one leaf device (a disk or a partition).

    Args:
        dev: The leaf's own ``maj:min``, when read.
        holders: What sits directly on the leaf.
        signature: What udev says the leaf is formatted as, when read.
        sources: The capture's cross-referencing tables.
        stacked: Every stacked device, keyed by kernel name.

    Returns:
        One use per direct mount, swap and signature match, plus one use per
        holder.
    """
    uses: list[DiskUse] = []
    if dev:
        mounts = sources.mounts_by_dev.get(dev, ())
        if mounts:
            uses.append(DiskUse(kind=UseKind.MOUNT, mounts=mounts))
        if dev in sources.swap_devs:
            uses.append(DiskUse(kind=UseKind.SWAP))
    if signature is not None and signature.fs_type == _ZFS_MEMBER and signature.fs_label:
        uses.append(DiskUse(kind=UseKind.ZFS, name=signature.fs_label))
    uses.extend(_stack_use(holder, stacked, sources) for holder in holders)
    return uses


def _merge(uses: list[DiskUse]) -> tuple[DiskUse, ...]:
    """Collapse repeated uses of the same kind and name into one.

    A disk with several partitions mounted straight from it reports one
    ``MOUNT`` use carrying every mountpoint, rather than one use per
    partition; the same collapse applies to any other kind two leaves agree
    on (two ZFS members of the same pool, for example).

    Args:
        uses: Every use found across a disk's leaves, in discovery order.

    Returns:
        One use per ``(kind, name)``, in first-appearance order, each
        carrying every mount any contributing use carried.
    """
    order: list[tuple[UseKind, str]] = []
    mounts_by_key: dict[tuple[UseKind, str], dict[str, None]] = {}
    for use in uses:
        key = (use.kind, use.name)
        if key not in mounts_by_key:
            order.append(key)
            mounts_by_key[key] = {}
        mounts_by_key[key].update(dict.fromkeys(use.mounts))
    return tuple(DiskUse(kind=kind, name=name, mounts=tuple(mounts_by_key[(kind, name)])) for kind, name in order)


def _signature_of(dev: str | None, capture: LinuxCapture) -> FilesystemSignature | None:
    """The udev signature for one device, when the udev database was read.

    Args:
        dev: The device's own ``maj:min``, when read.
        capture: A Linux reading.

    Returns:
        The signature, or ``None`` when there was none, the device was not
        read, or the udev database itself was absent.
    """
    if dev is None or capture.signatures is None:
        return None
    return capture.signatures.get(dev)


def _partition_signatures(block: BlockEntry, capture: LinuxCapture) -> list[FilesystemSignature | None]:
    """The udev signature of each partition of one disk, in partition order."""
    return [_signature_of(partition.dev, capture) for partition in (block.partitions or {}).values()]


def _whole_signature(
    block: BlockEntry,
    partition_signatures: list[FilesystemSignature | None],
    capture: LinuxCapture,
) -> FilesystemSignature | None:
    """The whole disk's signature, consulted only when no partition carries one.

    A filesystem that runs to the end of its disk (ZFS keeps labels there)
    makes the whole disk look like a member too, so a partition's own
    signature beats it. This is the one place that rule is written.
    """
    if any(signature is not None for signature in partition_signatures):
        return None
    return _signature_of(block.dev, capture)


def _signatures_of(block: BlockEntry, capture: LinuxCapture) -> list[FilesystemSignature | None]:
    """The signatures that count for one disk: its partitions', else the whole disk's."""
    partition_signatures = _partition_signatures(block, capture)
    whole = _whole_signature(block, partition_signatures, capture)
    return [whole] if whole is not None else partition_signatures


def _is_boot(merged: tuple[DiskUse, ...], pools: frozenset[str], boot_pools: frozenset[str]) -> bool:
    """Whether a disk's merged uses, or the pools its members belong to, mark it as a boot disk.

    Args:
        merged: A disk's merged uses.
        pools: Every ZFS pool a member on the disk names, through the holder
            closure as well (see :func:`_member_signatures`), so a root pool
            behind dm-crypt still marks the disk beneath it.
        boot_pools: ZFS pool names the machine boots from.

    Returns:
        Whether any use is mounted under a boot mountpoint, or the disk
        carries a member of a boot pool.
    """
    under_boot_mount = any(mountpoint in BOOT_MOUNTS for use in merged for mountpoint in use.mounts)
    return under_boot_mount or not pools.isdisjoint(boot_pools)


def _undecidable(
    block: BlockEntry,
    capture: LinuxCapture,
    signatures: list[FilesystemSignature | None],
) -> bool:
    """Whether a disk that resolved nothing could still be in use.

    "Not mounted" is claimed only when every source that could have named the
    disk was read, and when nothing on it is a member of a filesystem whose
    other members mountinfo cannot reach.

    Args:
        block: The disk's block entry.
        capture: A Linux reading.
        signatures: The signatures that counted for this disk's leaves.

    Returns:
        Whether an empty reading of this disk must be reported as not read.
    """
    partitions = block.partitions
    unread = (
        capture.signatures is None,
        capture.swaps is None,
        block.dev is None,
        partitions is None,
        partitions is not None and any(partition.dev is None for partition in partitions.values()),
    )
    unjoinable = any(sig is not None and sig.fs_type in _UNJOINABLE_MEMBER_TYPES for sig in signatures)
    return any(unread) or unjoinable


def _disk_usage(block: BlockEntry, capture: LinuxCapture, sources: _Sources) -> DiskUsage | None:
    """What one disk is used for.

    The whole disk is always a leaf: a holder, mount or swap on the raw device
    (a multipath path, a whole-disk md member) counts even when a leftover
    partition table is still there. Each partition is a leaf too. A
    partition's own udev signature beats the whole disk's: the whole-disk
    signature is consulted only when no partition carries one, because a
    filesystem that runs to the end of its disk (ZFS keeps labels there) makes
    the whole disk look like a member too.

    Args:
        block: The disk's block entry.
        capture: A Linux reading.
        sources: The capture's cross-referencing tables.

    Returns:
        The disk's usage, or ``None`` when nothing was found and the reading
        cannot rule out a use it did not see (see :func:`_undecidable`).
    """
    partitions = tuple((block.partitions or {}).values())
    partition_signatures = _partition_signatures(block, capture)
    whole_signature = _whole_signature(block, partition_signatures, capture)

    uses = _leaf_uses(block.dev, block.holders, whole_signature, sources, capture.stacked)
    for partition, signature in zip(partitions, partition_signatures, strict=True):
        uses.extend(_leaf_uses(partition.dev, partition.holders, signature, sources, capture.stacked))

    merged = _merge(uses)
    if not merged and _undecidable(block, capture, [whole_signature, *partition_signatures]):
        return None
    pools = frozenset(signature.fs_label for signature in _member_signatures(block, capture) if signature.fs_label)
    return DiskUsage(boot=_is_boot(merged, pools, sources.boot_pools), uses=merged)


def _leaves(block: BlockEntry) -> list[tuple[str | None, tuple[str, ...]]]:
    """One disk's own device and each of its partitions, with what sits directly on each."""
    return [(block.dev, block.holders), *((p.dev, p.holders) for p in (block.partitions or {}).values())]


def _stacked_devs(block: BlockEntry, stacked: Mapping[str, StackedEntry]) -> set[str]:
    """Every maj:min of a stacked device above one disk, and of that device's partitions."""
    devs: set[str] = set()
    for _dev, holders in _leaves(block):
        for holder in holders:
            for member in (holder, *_closure(holder, stacked)):
                devs.update(_devs_of(stacked.get(member, StackedEntry())))
    return devs


def _member_devs(block: BlockEntry, stacked: Mapping[str, StackedEntry]) -> set[str]:
    """Every maj:min a mount or a swap could name that lives on one disk."""
    return {dev for dev, _holders in _leaves(block) if dev} | _stacked_devs(block, stacked)


def disks_by_device(capture: LinuxCapture) -> dict[str, frozenset[str]]:
    """Which disks each device number lives on, the inverse of what a disk is used by.

    Built from the same holder closure as :func:`resolve_usage`, so a mount and
    the ``used by`` cell can never disagree about which disk holds it.

    Args:
        capture: A Linux reading.

    Returns:
        Every ``maj:min`` a mount could name (a disk's own, its partitions' and
        every stacked device above them) to the disk nodes it lives on.
        Virtual disks are left out.
    """
    found: dict[str, set[str]] = {}
    for node, block in capture.block.items():
        if block.virtual:
            continue
        for dev in _member_devs(block, capture.stacked):
            found.setdefault(dev, set()).add(node)
    return {dev: frozenset(nodes) for dev, nodes in found.items()}


def devices_on_virtual_disks(capture: LinuxCapture) -> frozenset[str]:
    """Every ``maj:min`` on a virtual disk (a loop, a zram, a zvol), by the closure ``disks_by_device`` follows.

    Args:
        capture: A Linux reading.

    Returns:
        Each virtual disk's own device number, its partitions' and every
        stacked device above them.
    """
    found: set[str] = set()
    for block in capture.block.values():
        if block.virtual:
            found |= _member_devs(block, capture.stacked)
    return frozenset(found)


def _member_signatures(block: BlockEntry, capture: LinuxCapture) -> list[FilesystemSignature]:
    """Every ZFS member signature on one disk: its own leaves', partition evidence first, then every stacked device's.

    A member on dm-crypt, LVM or md carries its signature on the stacked device
    rather than on the partition beneath it, so the holder closure
    ``disks_by_device`` follows counts too.
    """
    stacked = [_signature_of(dev, capture) for dev in sorted(_stacked_devs(block, capture.stacked))]
    return [
        signature
        for signature in (*_signatures_of(block, capture), *stacked)
        if signature is not None and signature.fs_type == _ZFS_MEMBER
    ]


def _disks_by_member(
    capture: LinuxCapture, key: Callable[[FilesystemSignature], str | None]
) -> dict[str, frozenset[str]]:
    """Which disks carry a ZFS member signature, grouped by ``key`` of that signature, partition evidence first."""
    found: dict[str, set[str]] = {}
    for node, block in capture.block.items():
        if block.virtual:
            continue
        for signature in _member_signatures(block, capture):
            group = key(signature)
            if group:
                found.setdefault(group, set()).add(node)
    return {group: frozenset(nodes) for group, nodes in found.items()}


def disks_by_pool_guid(capture: LinuxCapture) -> dict[str, frozenset[str]]:
    """Which disks carry a ZFS member, by the GUID of the pool its label belongs to.

    The same walk as :func:`disks_by_pool`, keyed by the label's pool GUID
    rather than its name, so two pools that share a name - a guest's root pool
    on a host volume beside the host's own - are told apart.

    Args:
        capture: A Linux reading.

    Returns:
        Pool GUID, in decimal as udev writes it, to the disk nodes carrying a member of it.
    """
    return _disks_by_member(capture, lambda signature: signature.fs_uuid)


def disks_by_pool(capture: LinuxCapture) -> dict[str, frozenset[str]]:
    """Which disks each ZFS pool's members sit on, partition evidence first.

    A member on dm-crypt, LVM or md carries its signature on the stacked device
    rather than on the partition beneath it, so every stacked device above a
    disk counts as well, through the same holder closure ``disks_by_device``
    follows. The partition-beats-whole-disk rule stays where it was written: a
    stacked device is not the disk's own leaf, so it never decides whether the
    whole-disk signature is consulted.

    Args:
        capture: A Linux reading.

    Returns:
        Pool name to the disk nodes carrying one of its members.
    """
    return _disks_by_member(capture, lambda signature: signature.fs_label)


def resolve_usage(capture: LinuxCapture, environment: Environment) -> dict[str, DiskUsage | None]:
    """Resolve what every non-virtual disk in a capture is used for.

    Args:
        capture: A Linux reading, live or replayed.
        environment: What kind of machine the reading came from.

    Returns:
        One entry per non-virtual block device, keyed by its node name. Every
        value is ``None`` in a container, where the host's own use of its
        disks is invisible, and when mountinfo itself could not be read.
    """
    if environment is Environment.CONTAINER or capture.mounts is None:
        return {node: None for node, block in capture.block.items() if not block.virtual}

    sources = _Sources(
        mounts_by_dev=_mounts_by_dev(capture),
        swap_devs=_swap_devs(capture),
        boot_pools=_boot_pools(capture),
    )
    return {node: _disk_usage(block, capture, sources) for node, block in capture.block.items() if not block.virtual}


__all__ = [
    "BOOT_MOUNTS",
    "devices_on_virtual_disks",
    "disks_by_device",
    "disks_by_pool",
    "disks_by_pool_guid",
    "resolve_usage",
]
