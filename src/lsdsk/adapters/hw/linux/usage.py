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
    from collections.abc import Mapping

#: Mountpoints whose disk the machine boots from.
BOOT_MOUNTS = frozenset({"/", "/boot", "/boot/efi"})

_ZFS_MEMBER = "zfs_member"

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
    ordered: dict[str, list[str]] = {}
    for mount in capture.mounts or ():
        for dev in (mount.dev, mount.source_dev):
            if not dev:
                continue
            bucket = ordered.setdefault(dev, [])
            if mount.mountpoint not in bucket:
                bucket.append(mount.mountpoint)
    return {dev: tuple(mountpoints) for dev, mountpoints in ordered.items()}


def _swap_devs(capture: LinuxCapture) -> frozenset[str]:
    """Every device that is active swap.

    Args:
        capture: A Linux reading.

    Returns:
        The ``maj:min`` of every swap that resolved to a device node.
    """
    return frozenset(swap.dev for swap in capture.swaps if swap.dev)


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
    queue = list(stacked.get(name, StackedEntry()).holders)
    while queue:
        candidate = queue.pop(0)
        if candidate in visited:
            continue
        visited.add(candidate)
        closure.append(candidate)
        entry = stacked.get(candidate)
        if entry is not None:
            queue.extend(entry.holders)
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
        return UseKind.LVM, _lvm_group(dm_name) if dm_name else ""
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
    mounts: list[str] = []
    is_swap = False
    for member in members:
        member_entry = stacked.get(member)
        dev = member_entry.dev if member_entry is not None else None
        if dev is None:
            continue
        for mountpoint in sources.mounts_by_dev.get(dev, ()):
            if mountpoint not in mounts:
                mounts.append(mountpoint)
        is_swap = is_swap or dev in sources.swap_devs
    if is_swap and "swap" not in mounts:
        mounts.append("swap")
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
    mounts_by_key: dict[tuple[UseKind, str], list[str]] = {}
    for use in uses:
        key = (use.kind, use.name)
        if key not in mounts_by_key:
            order.append(key)
            mounts_by_key[key] = []
        for mountpoint in use.mounts:
            if mountpoint not in mounts_by_key[key]:
                mounts_by_key[key].append(mountpoint)
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


def _is_boot(merged: tuple[DiskUse, ...], boot_pools: frozenset[str]) -> bool:
    """Whether a disk's merged uses mark it as a boot disk.

    Args:
        merged: A disk's merged uses.
        boot_pools: ZFS pool names the machine boots from.

    Returns:
        Whether any use is mounted under a boot mountpoint, or is a ZFS
        member of a boot pool.
    """
    under_boot_mount = any(mountpoint in BOOT_MOUNTS for use in merged for mountpoint in use.mounts)
    boot_pool_member = any(use.kind == UseKind.ZFS and use.name in boot_pools for use in merged)
    return under_boot_mount or boot_pool_member


def _disk_usage(block: BlockEntry, capture: LinuxCapture, sources: _Sources) -> DiskUsage | None:
    """What one disk is used for.

    The disk's own partitions are its leaves when it has any; a disk with no
    partition table is its own single leaf. A partition's own udev signature
    always beats the whole disk's: the whole-disk signature is read only in
    the no-partitions branch, never alongside a partition's.

    Args:
        block: The disk's block entry.
        capture: A Linux reading.
        sources: The capture's cross-referencing tables.

    Returns:
        The disk's usage, or ``None`` when nothing was found and the udev
        database was absent, so an empty reading cannot be told from one
        that simply never got to look.
    """
    uses: list[DiskUse] = []
    if block.partitions:
        for partition in block.partitions.values():
            signature = _signature_of(partition.dev, capture)
            uses.extend(_leaf_uses(partition.dev, partition.holders, signature, sources, capture.stacked))
    else:
        signature = _signature_of(block.dev, capture)
        uses.extend(_leaf_uses(block.dev, block.holders, signature, sources, capture.stacked))

    merged = _merge(uses)
    if not merged and capture.signatures is None:
        return None
    return DiskUsage(boot=_is_boot(merged, sources.boot_pools), uses=merged)


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


__all__ = ["BOOT_MOUNTS", "resolve_usage"]
