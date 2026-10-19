"""Read the Linux sources that say what uses each disk.

Every function takes the root it reads from, so tests drive it over a fake
tree and it stays in coverage, unlike the ioctl transport beside it. Nothing
here needs privilege: mountinfo, /proc/swaps, sysfs and the udev database are
all world-readable.

System Role:
    Adapter layer, reading half. Produces the plain mappings that
    :mod:`.capture` types and :mod:`.usage` resolves into what uses each disk;
    this module records sources only, and only the parts of them the resolver
    reads, so a capture carries no network share, home-directory FUSE mount or
    volume UUID.

Contents:
    * :func:`read_mounts` - every mounted filesystem, from mountinfo.
    * :func:`read_space` - each mounted filesystem's size, free space and inodes.
    * :func:`read_swaps` - every active swap, from ``/proc/swaps``.
    * :func:`read_partitions` - a disk's partitions and what sits on each.
    * :func:`read_stacked` - a device-mapper device's mapping and its holders.
    * :func:`stacked_device_numbers` - every device number a stacked reading names.
    * :func:`read_signatures` - the udev database's filesystem type and label.
    * :func:`read_btrfs_devices` - the member devices of each mounted btrfs.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from ....domain.errors import ConfigurationError, MissingFileError
from ...textfile import read_text_bounded
from .capture import is_fuse, replaced_surrogates

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

# mountinfo on a container host with a ZFS root runs to tens of thousands of
# characters for a few dozen mounts; a docker host with thousands of bind
# mounts is the real ceiling this guards against, not the ordinary case. It is
# the same order of magnitude as the other sysfs text bounds in this adapter
# and far below a size that would matter.
#
# This is the EFFECTIVE bound :func:`read_mounts` and :func:`read_swaps`
# enforce, well under :data:`..textfile.read_text_bounded`'s own 64 MiB
# ceiling: that ceiling exists to catch a mistyped path to an unrelated huge
# file, not to say a sane mountinfo or swap list could ever approach it. A
# file larger than this bound is treated exactly like one that could not be
# read at all - ``None`` from :func:`read_mounts` and :func:`read_swaps` alike
# - because this reader has no way to tell "this is really mountinfo and it is
# huge" from "this is not mountinfo", and the honest answer to either is the
# same one an unreadable file gets.
MAX_MOUNTINFO_BYTES = 8 * 1024 * 1024

#: The most characters a single short sysfs attribute (a device number, a dm
#: name or uuid, a udev database entry) is read as. The kernel caps a text
#: attribute at one page; this is the same ceiling :mod:`..linux.reader`
#: already applies to such an attribute, so nothing a live read keeps here is
#: refused for a reason the reader beside it would accept. Named for what it
#: counts: the read happens in text mode (``path.open("r")``), so the ceiling
#: is on decoded characters, not encoded bytes - the two differ for any
#: attribute holding non-ASCII text.
MAX_ATTRIBUTE_CHARS = 1024 * 1024

# mountinfo and the swap list carry paths, and a Linux path is any bytes, so a
# folder named in a legacy encoding puts invalid UTF-8 into either file. A strict
# decode would fail the whole scan over one row. mountinfo keeps each bad byte as
# a lone surrogate: its mountpoints are ASKED (statvfs, the free-extent walk), and
# only the path's own bytes reach that directory: kernel_path turns the text back
# into exactly those bytes, whatever the reading process's locale is. The capture
# model shows such a byte as a printable escape. The size bound is measured on those same bytes, so
# it is the file's own size. The decode covers the whole line, but only the
# mountpoint is asked at: every other field a row records has each such byte
# replaced (see _recorded), because a lone surrogate there - a FUSE subtype is
# whatever the mounting user chose - is text no capture can carry.
_MOUNTINFO_DECODE_ERRORS = "surrogateescape"
# The one field of a row that keeps its lone surrogates, so it can be asked at.
_ASKED_FIELD = "mountpoint"
# The swap list is never asked at a path: a swap FILE's path is dropped and a swap
# DEVICE is a /dev node, so replacing a bad byte there changes nothing anything
# reads, and keeps a lone surrogate out of the one path text a capture records.
# The size bound is then measured on the replaced text, a little larger than the
# file, which only makes it refuse sooner.
_PATH_DECODE_ERRORS = "replace"
# How a mountpoint read from mountinfo becomes the bytes the kernel wrote.
_KERNEL_PATH_ENCODING = "utf-8"
# The kernel writes a space, tab, newline or backslash in a mount path as a
# three-digit octal escape, so "/boot efi" arrives as "/boot\040efi".
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
# The udev properties this tool reads. The database also holds serials and
# paths; keeping only these keeps a capture's new fields free of identifiers.
_SIGNATURE_KEYS = {"ID_FS_TYPE": "fs_type", "ID_FS_LABEL": "fs_label"}
# A ZFS member's ID_FS_UUID is its pool's GUID, which tells two pools of one
# name apart; any other filesystem's UUID is nothing this tool reads.
_ZFS_MEMBER = "zfs_member"
_POOL_GUID_KEY = "ID_FS_UUID"

# mountinfo's fields left of " - " run id, parent id, maj:min, root, mountpoint,
# options[, optional tags]; field 4 (index 4, after a whitespace split) is the
# mountpoint, so a row with fewer than five fields cannot carry one.
_MIN_LEFT_FIELDS = 5
# The fields right of " - " are fstype, source[, options]; at least the first
# two are required to know the filesystem and where it came from.
_MIN_RIGHT_FIELDS = 2
# Index of the per-mount options in the left fields, and of the superblock
# options in the right fields (fstype, source, options).
_OPTIONS_FIELD = 5
_SUPER_OPTIONS_FIELD = 2
# A ZFS dataset has an anonymous device number, so its source is the one way to
# join it to its pool; every other source is either a device path (already
# resolved to source_dev) or something no disk sits behind.
_ZFS = "zfs"
# The key whose presence marks a device as read: an error entry lacks it.
_FIGURES_KEY = "frsize"
# Why a filesystem every one of whose mountpoints is hidden was not asked:
# statvfs resolves a path to whatever mount lookup reaches there, so it would
# answer for that filesystem rather than this one.
_HIDDEN_REASON = "another mount hides every mountpoint of it, so statvfs there would read a different filesystem"
# The mountpoint of the process root, where every path lookup starts.
_ROOT = "/"
# mountinfo's field 0 is the mount's own ID and field 1 its parent's.
_MOUNT_ID_FIELD = 0
_PARENT_ID_FIELD = 1
_MOUNTPOINT_FIELD = 4
# Major 0 is the kernel's anonymous device range: tmpfs, NFS, FUSE, overlay and
# every other mount with no block device behind it, plus btrfs and ZFS, whose
# real devices are found another way.
_ANONYMOUS_MAJOR = "0"
# Fedora/anaconda names a LUKS mapping "luks-<the volume's own UUID>" by
# default; a user-chosen name such as "cryptroot" carries no such identifier
# and is left alone.
_LUKS_UUID_MAPPING_NAME = re.compile(
    r"^luks-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def kernel_path(text: str) -> bytes:
    r"""The bytes a mountpoint :func:`read_mounts` returned stands for, to ask the system at.

    A ``str`` path would be encoded with the reading process's filesystem
    encoding, which differs from the UTF-8 mountinfo was decoded with under a
    legacy locale, and a byte that is not UTF-8 has no ``str`` spelling the
    system would map back to it at all.

    Args:
        text: A mountpoint as :func:`read_mounts` returned it.

    Returns:
        The path's bytes, each lone surrogate back to the byte it stood for.

    Example:
        >>> kernel_path("/M\udcfcll")
        b'/M\xfcll'
        >>> kernel_path("/gr\u00fcn")
        b'/gr\xc3\xbcn'
    """
    return text.encode(_KERNEL_PATH_ENCODING, _MOUNTINFO_DECODE_ERRORS)


def _unescape(text: str) -> str:
    """Undo the kernel's octal escaping of a mountinfo path field."""
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), text)


class _NodeStatus(Protocol):
    """The two fields of a ``stat`` result that name a device node."""

    @property
    def st_mode(self) -> int:
        """The file type and permission bits."""
        ...

    @property
    def st_rdev(self) -> int:
        """The device number a device node stands for."""
        ...


def device_number_of(path: str, *, stat_of: Callable[[str], _NodeStatus] = os.stat) -> str | None:
    """The ``maj:min`` of a device node, or None when it is not one.

    Args:
        path: A path that may name a device node, such as ``/dev/sda2``.
        stat_of: Reads the node's status, overridable because no unprivileged
            test can create a block device node.

    Returns:
        ``"<major>:<minor>"``, or ``None`` when the path cannot be stat'd or
        names something other than a block device. A character device such as
        ``/dev/null`` or ``/dev/fuse`` has a device number too, from a separate
        numbering, so taking it would name a block device it has nothing to do
        with.
    """
    try:
        status = stat_of(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISBLK(status.st_mode) or not status.st_rdev:
        return None
    return f"{os.major(status.st_rdev)}:{os.minor(status.st_rdev)}"


def read_mounts(
    path: Path = Path("/proc/self/mountinfo"),
    *,
    limit: int = MAX_MOUNTINFO_BYTES,
    device_number: Callable[[str], str | None] = device_number_of,
) -> list[dict[str, str | bool]] | None:
    """Read every mounted filesystem from mountinfo.

    Args:
        path: The mountinfo file to read, overridable for a test.
        limit: The most UTF-8 bytes this file is accepted as, overridable for
            a test. See :data:`MAX_MOUNTINFO_BYTES` for the shipped figure and
            why a file past it is refused the same way an unreadable one is.
        device_number: Resolves a ``/dev`` source to its block device number,
            overridable for a test, since a machine running the suite need
            not have a block device node at all.

    Returns:
        One row per mount a disk can sit behind, each carrying ``dev`` (the
        mounted device's own ``maj:min``, field 3), ``mountpoint`` (a byte that
        is not UTF-8 kept as a lone surrogate, so :func:`kernel_path` gives
        back the path's own bytes; in every other field such a byte is
        U+FFFD) and
        ``fstype`` and ``read_only`` (a real boolean, lsdsk's own verdict rather
        than text the kernel published), plus ``source_dev`` when the source
        names a resolvable ``/dev`` node and, for ZFS only, ``source`` narrowed to the pool name,
        and ``hidden`` (``True``, present only then) when no path reaches the mountpoint because
        of another mount (see :func:`_hidden_mount_ids`) - decided over EVERY row, the dropped
        ones included, since a tmpfs mounted over a disk's mountpoint hides it as surely as a
        second disk does.
        A row with an anonymous device number and no resolvable source (tmpfs,
        NFS, FUSE, overlay) is left out, and so is every other source text: an
        NFS or sshfs source names a host and a user, and nothing reads it.
        ``None`` when the file could not be read at all, or is larger than
        `limit` - a different fact from a machine with no mounts, which
        mountinfo never reports.
    """
    try:
        text = read_text_bounded(path, what="mountinfo", errors=_MOUNTINFO_DECODE_ERRORS)
    except (MissingFileError, ConfigurationError):
        return None
    if len(kernel_path(text)) > limit:
        return None

    lines = [line for line in text.split("\n") if line]
    hidden = _hidden_mount_ids(lines)
    rows: list[dict[str, str | bool]] = []
    for line in lines:
        row = _parse_mountinfo_line(line, device_number)
        if row is None:
            continue
        if line.split(" ", 1)[0] in hidden:
            row["hidden"] = True
        rows.append(_recorded(row))
    return rows


def _recorded(row: dict[str, str | bool]) -> dict[str, str | bool]:
    r"""`row` as a capture records it: no field but the mountpoint keeps a lone surrogate.

    Applied to the finished row rather than field by field, so a field a later
    change adds cannot forget it. The source was already resolved at its own
    bytes, so replacing a byte here changes nothing anything asks.

    Example:
        >>> row = _recorded({"mountpoint": "/caf\udce9", "fstype": "fuseblk.\udcff"})
        >>> row["mountpoint"], row["fstype"] == "fuseblk.\ufffd"
        ('/caf\udce9', True)
    """
    return {
        key: replaced_surrogates(value) if isinstance(value, str) and key != _ASKED_FIELD else value
        for key, value in row.items()
    }


class _MountIds(NamedTuple):
    """Where one mountinfo row sits in the mount tree: three strings a swap would silently confuse."""

    mount: str
    parent: str
    mountpoint: str


def _mount_ids(line: str) -> _MountIds | None:
    """A mountinfo row's own mount ID, its parent's ID and its mountpoint, or ``None`` when malformed."""
    left, separator, _right = line.partition(" - ")
    fields = left.split(" ")
    if not separator or len(fields) < _MIN_LEFT_FIELDS:
        return None
    return _MountIds(
        mount=fields[_MOUNT_ID_FIELD],
        parent=fields[_PARENT_ID_FIELD],
        mountpoint=_unescape(fields[_MOUNTPOINT_FIELD]),
    )


def _strict_prefixes(path: str) -> list[str]:
    """Every directory above ``path``, component by component: ``/a/b`` gives ``/`` and ``/a``."""
    if path == _ROOT:
        return []
    parts = path.split("/")
    return [_ROOT, *("/".join(parts[:end]) for end in range(2, len(parts)))]


def _on_a_chroot_directory(ids: Sequence[_MountIds], point: Mapping[str, str]) -> set[str]:
    """The mounts at ``/`` stacked on a process root that is a plain directory of an unlisted mount.

    Chrooted into a directory that is not itself a mountpoint, the mount the
    directory belongs to lies outside the root and is not listed, so every
    mount made inside the chroot names it as an unlisted parent. A mount later
    placed on the chroot directory is listed at ``/`` under that same parent,
    while lookup still starts at the directory beneath it. A true root's
    unlisted parent carries nothing else that is listed, so sharing it with
    another listed mount is what tells the two apart.
    """
    children: dict[str, int] = {}
    for entry in ids:
        children[entry.parent] = children.get(entry.parent, 0) + 1
    return {
        entry.mount
        for entry in ids
        if entry.mountpoint == _ROOT and entry.parent not in point and children[entry.parent] > 1
    }


def _hidden_mount_ids(lines: Iterable[str]) -> set[str]:
    """The IDs of every mount whose mountpoint no path reaches.

    Decided from mountinfo's own structure, never from ``st_dev`` (a btrfs
    subvolume reports an anonymous one that differs from mountinfo's). Path
    lookup walks a mountpoint's directories inside its parent mount and crosses
    into whatever is mounted on one of them, so a mount is CUT OFF when another
    mount with the same parent sits on a directory strictly above its
    mountpoint - whichever was mounted first, since lookup crosses the mount on
    the directory either way. That covers both a mount on an ancestor directory
    and a mount stacked on the parent's own mountpoint, which is the parent's
    root directory. A mount is UNREACHABLE when it is cut off or its parent's
    mountpoint is unreachable, and COVERED when a mount is stacked on its own
    mountpoint; hidden is either.

    The process root is the exception: lookup starts AT it rather than crossing
    into it, so a mount stacked on ``/`` is the unreachable one, and it hides
    nothing beneath the root. That holds too for a mount placed on a chroot's
    own directory (see :func:`_on_a_chroot_directory`), which mountinfo lists
    at ``/`` beside the chroot's other mounts. The walk is linear and stops on
    a cycle, since the file is read rather than trusted.
    """
    ids = [entry for entry in map(_mount_ids, lines) if entry is not None]
    point = {entry.mount: entry.mountpoint for entry in ids}
    parent = {entry.mount: entry.parent for entry in ids}
    on_chroot_directory = _on_a_chroot_directory(ids, point)
    roots = {
        entry.mount
        for entry in ids
        if entry.mountpoint == _ROOT
        and (entry.parent not in point or entry.parent == entry.mount)
        and entry.mount not in on_chroot_directory
    }
    stacked = {
        entry.mount for entry in ids if entry.parent != entry.mount and point.get(entry.parent) == entry.mountpoint
    }
    on_root = {mount for mount in stacked if parent[mount] in roots} | on_chroot_directory
    covered = {parent[mount] for mount in stacked - on_root}
    # A mount that is its own parent sits on no directory of a parent mount, so
    # it occupies nothing: counting it would cut off every mount below it.
    occupied = {
        (entry.parent, entry.mountpoint) for entry in ids if entry.mount not in on_root and entry.parent != entry.mount
    }
    cut_off = on_root | {
        entry.mount
        for entry in ids
        if any((entry.parent, above) in occupied for above in _strict_prefixes(entry.mountpoint))
    }
    unreachable: dict[str, bool] = {}
    for start in parent:
        chain: list[str] = []
        on_chain: set[str] = set()
        node = start
        while node not in unreachable:
            up = parent[node]
            if up not in parent or up == node or up in on_chain:
                unreachable[node] = node in cut_off
                break
            chain.append(node)
            on_chain.add(node)
            node = up
        for child in reversed(chain):
            unreachable[child] = unreachable[parent[child]] or child in cut_off
    return covered | {mount for mount, is_unreachable in unreachable.items() if is_unreachable}


def _parse_mountinfo_line(line: str, device_number: Callable[[str], str | None]) -> dict[str, str | bool] | None:
    """Parse one mountinfo row, or ``None`` for a malformed or unwanted one.

    The optional fields between field 6 and the separator vary in count, so
    the line is split on the FIRST ``" - "`` that follows field 6 rather than
    on a fixed index counted from the right. A row no disk can sit behind is
    ``None`` too (see :func:`read_mounts`).
    """
    left, separator, right = line.partition(" - ")
    if not separator:
        return None
    # Single spaces separate the fields and a path never holds one (the kernel
    # escapes it), whereas a bare split() would also cut at form feed, U+2028...
    left_fields = left.split(" ")
    right_fields = right.split(" ", 2)
    if len(left_fields) < _MIN_LEFT_FIELDS or len(right_fields) < _MIN_RIGHT_FIELDS:
        return None

    dev, mountpoint, fstype = left_fields[2], _unescape(left_fields[4]), right_fields[0]
    row: dict[str, str | bool] = {"dev": dev, "mountpoint": mountpoint, "fstype": fstype}
    source = _unescape(right_fields[1])
    source_dev = device_number(source) if source.startswith("/dev/") and not is_fuse(fstype) else None
    if source_dev is not None:
        row["source_dev"] = source_dev
    if fstype == _ZFS:
        # The dataset path below the pool is never read, and can name a user.
        row["source"] = source.split("/", 1)[0]
    elif dev.split(":", 1)[0] == _ANONYMOUS_MAJOR and source_dev is None:
        return None
    # Field 6 carries the per-mount options and the field after the source the
    # superblock's; either one being read-only makes this mount read-only.
    per_mount = left_fields[_OPTIONS_FIELD] if len(left_fields) > _OPTIONS_FIELD else ""
    super_options = right_fields[_SUPER_OPTIONS_FIELD] if len(right_fields) > _SUPER_OPTIONS_FIELD else ""
    row["read_only"] = _starts_read_only(per_mount) or _starts_read_only(super_options)
    return row


def _starts_read_only(options: str) -> bool:
    """Whether a comma-separated option list says ``ro``."""
    # The kernel always emits ``ro`` or ``rw`` first, so the first token decides.
    return options.split(",", 1)[0] == "ro"


class StatvfsResult(Protocol):
    """The six figures read from ``os.statvfs``."""

    @property
    def f_frsize(self) -> int:
        """The fundamental block size, in bytes."""
        ...

    @property
    def f_blocks(self) -> int:
        """The size of the filesystem, in ``f_frsize`` blocks."""
        ...

    @property
    def f_bfree(self) -> int:
        """The free blocks, including those reserved for root."""
        ...

    @property
    def f_bavail(self) -> int:
        """The free blocks an unprivileged user can use."""
        ...

    @property
    def f_files(self) -> int:
        """The total inodes."""
        ...

    @property
    def f_ffree(self) -> int:
        """The free inodes."""
        ...


def _system_statvfs(path: str) -> StatvfsResult:
    """Ask the system at the path's own bytes, resolving ``os.statvfs`` at call time.

    ``os.statvfs`` does not exist on Windows, so naming it as a default would
    break the import of this module there.
    """
    return os.statvfs(kernel_path(path))


def read_space(
    rows: Sequence[Mapping[str, str | bool]], *, statvfs: Callable[[str], StatvfsResult] = _system_statvfs
) -> dict[str, dict[str, int | str]]:
    """Read each mounted filesystem's size, free space and inodes once.

    Only the rows ``read_mounts`` kept are asked, which already excludes every
    mount no disk can sit behind (tmpfs, NFS, FUSE), so a hung network mount is
    never stat-ed. ZFS datasets are skipped: a dataset's statvfs reports its own
    data plus the pool's free space, never the pool's fill.

    A device is asked at its next mountpoint only after a refusal: every
    mountpoint of one device shares its figures, so the first success settles
    it and a later mountpoint is never asked. A ``hidden`` mountpoint is never
    asked, because ``statvfs`` resolves it to the mount covering it and would
    answer for that one; a device met only there is recorded as refused, with
    the reason, rather than given another filesystem's figures.

    Args:
        rows: What :func:`read_mounts` returned.
        statvfs: The call that reads one mountpoint.

    Returns:
        One reading per filesystem device, keyed by its ``maj:min``. A device
        whose every mountpoint refused is ``{"error": <first reason>}``, and
        one whose every mountpoint is hidden says so the same way.
    """
    readings: dict[str, dict[str, int | str]] = {}
    hidden_only: list[str] = []
    for row in rows:
        dev, mountpoint = row.get("dev", ""), row.get("mountpoint", "")
        if not isinstance(dev, str) or not isinstance(mountpoint, str):
            continue
        if not dev or row.get("fstype") == _ZFS or _FIGURES_KEY in readings.get(dev, {}):
            continue
        if row.get("hidden") is True:
            hidden_only.append(dev)
            continue
        reading = _ask(mountpoint, statvfs)
        if "error" in reading and dev in readings:
            continue
        readings[dev] = reading
    for dev in hidden_only:
        readings.setdefault(dev, {"error": _HIDDEN_REASON})
    return readings


def _ask(mountpoint: str, statvfs: Callable[[str], StatvfsResult]) -> dict[str, int | str]:
    """One mountpoint's figures, or ``{"error": <reason>}`` when it refused."""
    try:
        result = statvfs(mountpoint)
    except OSError as error:
        return {"error": error.strerror or str(error)}
    return {
        _FIGURES_KEY: result.f_frsize,
        "blocks": result.f_blocks,
        "bfree": result.f_bfree,
        "bavail": result.f_bavail,
        "files": result.f_files,
        "ffree": result.f_ffree,
    }


def read_swaps(
    path: Path = Path("/proc/swaps"),
    *,
    limit: int = MAX_MOUNTINFO_BYTES,
    device_number: Callable[[str], str | None] = device_number_of,
) -> list[dict[str, str]] | None:
    """Read every active swap from ``/proc/swaps``.

    Args:
        path: The swaps file to read, overridable for a test.
        limit: The most UTF-8 bytes this file is accepted as, overridable for
            a test. See :data:`MAX_MOUNTINFO_BYTES` for the shipped figure;
            the swap list shares it with mountinfo rather than having its own,
            since both are the same order of magnitude of sysfs-adjacent text.
        device_number: Resolves a swap entry to its block device number,
            overridable for a test, as in :func:`read_mounts`.

    Returns:
        One row per swap. A row whose entry resolves to a device node carries
        ``path`` and ``dev``; a swap FILE's path is read by nothing
        downstream (:mod:`.usage` joins only on ``dev``) and can name a user
        or a project, so it is recorded as an empty row instead. Empty when
        the file has no swaps beyond its header; ``None`` when it cannot be
        read or is larger than `limit`, because an unread swap list cannot
        say a disk carries no swap.
    """
    try:
        text = read_text_bounded(path, what="the swap list", errors=_PATH_DECODE_ERRORS)
    except (MissingFileError, ConfigurationError):
        return None
    if len(text.encode("utf-8")) > limit:
        return None

    rows: list[dict[str, str]] = []
    for line in text.split("\n")[1:]:
        fields = [field for field in re.split(r"[ \t]+", line) if field]
        if not fields:
            continue
        # The kernel escapes space, tab, newline and backslash the same way in
        # every seq_file path it publishes (seq_file_path), /proc/swaps field 0
        # included, so it must be undone here exactly as read_mounts does for
        # mountinfo - otherwise a swap on a path carrying one of those bytes
        # never resolves to its device node.
        swap_path = _unescape(fields[0])
        dev = device_number(swap_path)
        rows.append({"path": swap_path, "dev": dev} if dev is not None else {})
    return rows


def read_partitions(node: Path) -> dict[str, dict[str, Any]] | None:
    """Read a disk's partitions and what sits directly on each.

    A sysfs disk directory holds other children too (``queue``, ``device``,
    ``holders``, ``power``, ...); only a child carrying its own ``partition``
    file is a partition.

    Args:
        node: The disk's (or a stacked device's) ``/sys/block`` directory.

    Returns:
        One entry per partition, keyed by its kernel name, each carrying
        ``dev`` when read and ``holders`` (sorted names of what sits on it).
        Empty when ``node`` is not a directory. ``None`` when it is one but
        could not be listed, so one unreadable disk degrades to "not read"
        for that disk alone instead of aborting the whole reading.
    """
    partitions: dict[str, dict[str, Any]] = {}
    try:
        if not node.is_dir():
            return partitions
        children = sorted(child for child in node.iterdir() if (child / "partition").is_file())
    except OSError:
        return None
    for child in children:
        entry: dict[str, Any] = {}
        dev = _read_short_text(child / "dev")
        if dev is not None:
            entry["dev"] = dev
        entry["holders"] = _holder_names(child)
        partitions[child.name] = entry
    return partitions


def read_stacked(names: Iterable[str], root: Path = Path("/sys/block")) -> dict[str, dict[str, Any]]:
    """Read every device-mapper device's mapping and what sits on it.

    Follows holders transitively, so a crypt-under-LVM chain is captured in
    one call: a holder of a seed device is read too, and its own holders in
    turn, each name visited once.

    Args:
        names: The device-mapper device names to start from (a disk's or a
            partition's holders).
        root: The ``/sys/block`` directory, overridable for a test.

    Returns:
        One entry per device-mapper device reached, keyed by kernel name, each
        carrying ``dev``, ``dm_name`` (a Fedora-style ``luks-<UUID>`` default
        name narrowed to ``"luks"``, any other name untouched) and the type
        prefix of ``dm_uuid`` (``LVM-``, ``CRYPT-``, ``mpath-``) where
        present, ``holders``, and
        ``partitions`` (a partitioned md array is mounted through these).
    """
    found: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        node = root / name
        if not node.is_dir():
            continue
        entry = _read_stacked_entry(node)
        found[name] = entry
        parts: dict[str, dict[str, Any]] = entry.get("partitions") or {}
        below = [*entry["holders"], *(h for part in parts.values() for h in part["holders"])]
        pending.extend(holder for holder in below if holder not in seen)
    return found


def stacked_device_numbers(stacked: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every device number a :func:`read_stacked` reading names: each device's own, then its partitions'.

    A ZFS pool on dm-crypt, LVM or md has its member signature on the stacked
    device rather than on the partition beneath it, so these are looked up in
    the udev database beside the disks' own numbers.

    Args:
        stacked: What :func:`read_stacked` returned.

    Returns:
        The numbers in reading order, without the ones that were not read.

    Example:
        >>> stacked_device_numbers({"md0": {"dev": "9:0", "partitions": {"md0p1": {"dev": "259:7"}}}})
        ['9:0', '259:7']
    """
    devnums: list[str] = []
    for entry in stacked.values():
        partitions: Mapping[str, Mapping[str, Any]] = entry.get("partitions") or {}
        candidates = (entry.get("dev"), *(partition.get("dev") for partition in partitions.values()))
        devnums.extend(dev for dev in candidates if isinstance(dev, str) and dev)
    return devnums


def _scrub_dm_name(dm_name: str) -> str:
    """Replace a Fedora-style ``luks-<UUID>`` mapping name with the literal ``"luks"``.

    Args:
        dm_name: The mapping name ``dm/name`` published.

    Returns:
        ``"luks"`` for the default anaconda name, unchanged otherwise - a
        user-chosen name such as ``cryptroot`` is not machine-unique by
        construction and carries no identifier to remove.

    Example:
        >>> _scrub_dm_name("luks-0123abcd-4567-89ef-0123-456789abcdef")
        'luks'
        >>> _scrub_dm_name("cryptroot")
        'cryptroot'
        >>> _scrub_dm_name("luks-notauuid")
        'luks-notauuid'
    """
    return "luks" if _LUKS_UUID_MAPPING_NAME.match(dm_name) else dm_name


def _read_stacked_entry(node: Path) -> dict[str, Any]:
    """Read one stacked device's number, mapping, holders and partitions."""
    entry: dict[str, Any] = {}
    dev = _read_short_text(node / "dev")
    if dev is not None:
        entry["dev"] = dev
    dm_name = _read_short_text(node / "dm" / "name")
    if dm_name is not None:
        entry["dm_name"] = _scrub_dm_name(dm_name)
    dm_uuid = _uuid_type(_read_short_text(node / "dm" / "uuid"))
    if dm_uuid is not None:
        entry["dm_uuid"] = dm_uuid
    entry["holders"] = _holder_names(node)
    # A stacked device whose own directory cannot be listed still names its
    # layer (dev, dm_name, dm_uuid, holders survive); its partitions key is
    # omitted so the capture reads as "not read" rather than as "no
    # partitions" - the same not-read/empty distinction BlockEntry keeps for
    # a disk, so a disk reached only through this device's unread partitions
    # stays undecidable instead of reading "not mounted".
    partitions = read_partitions(node)
    if partitions is not None:
        entry["partitions"] = partitions
    return entry


def _uuid_type(dm_uuid: str | None) -> str | None:
    """The type prefix of a device-mapper UUID, without the identifier after it.

    Args:
        dm_uuid: A mapping UUID such as ``LVM-<pv uuid><lv uuid>`` or
            ``CRYPT-LUKS2-<uuid>-<name>``, when one was read.

    Returns:
        The first dash-separated field with its dash (``LVM-``, ``CRYPT-``),
        which is all the resolver reads, or ``None`` when there is none.

    Example:
        >>> _uuid_type("CRYPT-LUKS2-0123abcd-cryptroot")
        'CRYPT-'
        >>> _uuid_type("nodash") is None
        True
    """
    if dm_uuid is None:
        return None
    kind, separator, _ = dm_uuid.partition("-")
    return f"{kind}-" if kind and separator else None


def read_signatures(devnums: Iterable[str], root: Path = Path("/run/udev/data")) -> dict[str, dict[str, str]] | None:
    """Read the udev database's filesystem type and label for each device, and a ZFS member's pool GUID.

    Args:
        devnums: The ``maj:min`` device numbers to look up.
        root: The udev database directory, overridable for a test.

    Returns:
        One entry per devnum that carries a filesystem type or label, keyed by
        devnum. ``None`` when ``root`` is not a directory at all - this
        container's own ``/run`` carries no udev database, which is a
        different fact from every device having none.
    """
    if not root.is_dir():
        return None

    signatures: dict[str, dict[str, str]] = {}
    for devnum in devnums:
        found = _udev_signature(root / f"b{devnum}")
        if found:
            signatures[devnum] = found
    return signatures


def _udev_signature(path: Path) -> dict[str, str]:
    """The filesystem type and label a udev database entry carries, and a ZFS member's pool GUID, if any."""
    text = _read_short_text(path, limit=MAX_ATTRIBUTE_CHARS)
    if text is None:
        return {}
    found: dict[str, str] = {}
    uuid: str | None = None
    for line in text.split("\n"):
        if not line.startswith("E:"):
            continue
        key, _, value = line[2:].partition("=")
        name = _SIGNATURE_KEYS.get(key)
        if name is not None:
            found[name] = value
        elif key == _POOL_GUID_KEY:
            uuid = value
    if uuid is not None and found.get("fs_type") == _ZFS_MEMBER:
        found["fs_uuid"] = uuid
    return found


def _holder_names(node: Path) -> list[str]:
    """The sorted names of what sits directly on a block device."""
    holders = node / "holders"
    try:
        return sorted(child.name for child in holders.iterdir())
    except OSError:
        return []


#: A sysfs ``dev`` attribute: the block device's ``maj:min``.
_DEVICE_NUMBER = re.compile(r"[0-9]{1,10}:[0-9]{1,10}")


def read_btrfs_devices(root: Path = Path("/sys/fs/btrfs")) -> list[list[str]] | None:
    """Read which devices each mounted btrfs filesystem spans.

    mountinfo names ONE device as a multi-device btrfs's source, so the other
    members cannot be joined to the mount from it. The kernel lists every
    member under ``<root>/<fsid>/devices/``, one entry per member linking to
    its block device, and creates that directory when the filesystem is
    mounted - so a member in no list is a member of no mounted filesystem.
    The fsid names the directory and is not recorded: nothing downstream
    needs it, and it identifies the filesystem.

    Args:
        root: The ``/sys/fs/btrfs`` directory, overridable for a test.

    Returns:
        Each mounted filesystem's member device numbers, sorted, the lists in
        sorted order. Empty when `root` does not exist, which is the btrfs
        module not loaded and so nothing mounted. ``None`` when any part could
        not be read: a member left out would read as not mounted, so a partial
        list is no reading at all.

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as empty:
        ...     read_btrfs_devices(Path(empty) / "btrfs")
        []
    """
    try:
        filesystems = sorted(root.iterdir())
    except FileNotFoundError:
        return []
    except OSError:
        return None
    groups: list[list[str]] = []
    for filesystem in filesystems:
        # ``features`` (and anything else that is not a filesystem) has no ``devices``.
        devices = filesystem / "devices"
        if not devices.is_dir():
            continue
        members = _btrfs_members(devices)
        if members is None:
            return None
        if members:
            groups.append(members)
    return sorted(groups)


def _btrfs_members(devices: Path) -> list[str] | None:
    """The device numbers under one filesystem's ``devices`` directory, or ``None`` if any is unread."""
    try:
        entries = sorted(devices.iterdir())
    except OSError:
        return None
    numbers = [_read_short_text(entry / "dev") for entry in entries]
    if not all(number is not None and _DEVICE_NUMBER.fullmatch(number) for number in numbers):
        return None
    return sorted(number for number in numbers if number is not None)


def _read_short_text(path: Path, *, limit: int = MAX_ATTRIBUTE_CHARS) -> str | None:
    """Read one short sysfs attribute's stripped text, or ``None``.

    Args:
        path: The attribute to read.
        limit: The most characters it may carry, in text mode. See
            :data:`MAX_ATTRIBUTE_CHARS`.
    """
    try:
        # newline="" keeps a carriage return the file holds: universal newlines would
        # turn it into the row break the caller now splits on, cutting a udev value short.
        # The kernel and udev write UTF-8 whatever the reading process's locale is.
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return None
    if len(raw) > limit:
        return None
    return raw.strip()


__all__ = [
    "MAX_ATTRIBUTE_CHARS",
    "MAX_MOUNTINFO_BYTES",
    "StatvfsResult",
    "device_number_of",
    "kernel_path",
    "read_btrfs_devices",
    "read_mounts",
    "read_partitions",
    "read_signatures",
    "read_space",
    "read_stacked",
    "read_swaps",
    "stacked_device_numbers",
]
