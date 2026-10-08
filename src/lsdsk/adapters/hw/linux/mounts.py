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
    * :func:`read_swaps` - every active swap, from ``/proc/swaps``.
    * :func:`read_partitions` - a disk's partitions and what sits on each.
    * :func:`read_stacked` - a device-mapper device's mapping and its holders.
    * :func:`read_signatures` - the udev database's filesystem type and label.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ....domain.errors import ConfigurationError, MissingFileError
from ...textfile import read_text_bounded

if TYPE_CHECKING:
    from collections.abc import Iterable

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
# decode would fail the whole scan over one row; replacing the bad bytes keeps
# every reading. Nothing joins or decides on the path text itself (device numbers
# and filesystem types are ASCII, and the boot paths compared against are ASCII
# too), so a replaced character changes only what is displayed. The size bound is
# then measured on the replaced text, a little larger than the file, which only
# makes it refuse sooner.
_PATH_DECODE_ERRORS = "replace"
# The kernel writes a space, tab, newline or backslash in a mount path as a
# three-digit octal escape, so "/boot efi" arrives as "/boot\040efi".
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
# The two udev properties this tool reads. The database also holds serials and
# paths; keeping only these keeps a capture's new fields free of identifiers.
_SIGNATURE_KEYS = {"ID_FS_TYPE": "fs_type", "ID_FS_LABEL": "fs_label"}

# mountinfo's fields left of " - " run id, parent id, maj:min, root, mountpoint,
# options[, optional tags]; field 4 (index 4, after a whitespace split) is the
# mountpoint, so a row with fewer than five fields cannot carry one.
_MIN_LEFT_FIELDS = 5
# The fields right of " - " are fstype, source[, options]; at least the first
# two are required to know the filesystem and where it came from.
_MIN_RIGHT_FIELDS = 2
# A ZFS dataset has an anonymous device number, so its source is the one way to
# join it to its pool; every other source is either a device path (already
# resolved to source_dev) or something no disk sits behind.
_ZFS = "zfs"
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


def _unescape(text: str) -> str:
    """Undo the kernel's octal escaping of a mountinfo path field."""
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), text)


def _device_number_of(path: str) -> str | None:
    """The ``maj:min`` of a device node, or None when it is not one.

    Args:
        path: A path that may name a device node, such as ``/dev/sda2``.

    Returns:
        ``"<major>:<minor>"``, or ``None`` when the path cannot be stat'd or
        names something other than a device.
    """
    try:
        rdev = Path(path).stat().st_rdev
    except OSError:
        return None
    return f"{os.major(rdev)}:{os.minor(rdev)}" if rdev else None


def read_mounts(
    path: Path = Path("/proc/self/mountinfo"),
    *,
    limit: int = MAX_MOUNTINFO_BYTES,
) -> list[dict[str, str]] | None:
    """Read every mounted filesystem from mountinfo.

    Args:
        path: The mountinfo file to read, overridable for a test.
        limit: The most UTF-8 bytes this file is accepted as, overridable for
            a test. See :data:`MAX_MOUNTINFO_BYTES` for the shipped figure and
            why a file past it is refused the same way an unreadable one is.

    Returns:
        One row per mount a disk can sit behind, each carrying ``dev`` (the
        mounted device's own ``maj:min``, field 3), ``mountpoint`` and
        ``fstype``, plus ``source_dev`` when the source names a resolvable
        ``/dev`` node and, for ZFS only, ``source`` narrowed to the pool name.
        A row with an anonymous device number and no resolvable source (tmpfs,
        NFS, FUSE, overlay) is left out, and so is every other source text: an
        NFS or sshfs source names a host and a user, and nothing reads it.
        ``None`` when the file could not be read at all, or is larger than
        `limit` - a different fact from a machine with no mounts, which
        mountinfo never reports.
    """
    try:
        text = read_text_bounded(path, what="mountinfo", errors=_PATH_DECODE_ERRORS)
    except (MissingFileError, ConfigurationError):
        return None
    if len(text.encode("utf-8")) > limit:
        return None

    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = _parse_mountinfo_line(line)
        if row is not None:
            rows.append(row)
    return rows


def _parse_mountinfo_line(line: str) -> dict[str, str] | None:
    """Parse one mountinfo row, or ``None`` for a malformed or unwanted one.

    The optional fields between field 6 and the separator vary in count, so
    the line is split on the FIRST ``" - "`` that follows field 6 rather than
    on a fixed index counted from the right. A row no disk can sit behind is
    ``None`` too (see :func:`read_mounts`).
    """
    left, separator, right = line.partition(" - ")
    if not separator:
        return None
    left_fields = left.split()
    right_fields = right.split(maxsplit=2)
    if len(left_fields) < _MIN_LEFT_FIELDS or len(right_fields) < _MIN_RIGHT_FIELDS:
        return None

    row = {"dev": left_fields[2], "mountpoint": _unescape(left_fields[4]), "fstype": right_fields[0]}
    source = _unescape(right_fields[1])
    source_dev = _device_number_of(source) if source.startswith("/dev/") else None
    if source_dev is not None:
        row["source_dev"] = source_dev
    if row["fstype"] == _ZFS:
        # The dataset path below the pool is never read, and can name a user.
        row["source"] = source.split("/", 1)[0]
    elif row["dev"].split(":", 1)[0] == _ANONYMOUS_MAJOR and source_dev is None:
        return None
    return row


def read_swaps(
    path: Path = Path("/proc/swaps"),
    *,
    limit: int = MAX_MOUNTINFO_BYTES,
) -> list[dict[str, str]] | None:
    """Read every active swap from ``/proc/swaps``.

    Args:
        path: The swaps file to read, overridable for a test.
        limit: The most UTF-8 bytes this file is accepted as, overridable for
            a test. See :data:`MAX_MOUNTINFO_BYTES` for the shipped figure;
            the swap list shares it with mountinfo rather than having its own,
            since both are the same order of magnitude of sysfs-adjacent text.

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
    for line in text.splitlines()[1:]:
        fields = line.split()
        if not fields:
            continue
        # The kernel escapes space, tab, newline and backslash the same way in
        # every seq_file path it publishes (seq_file_path), /proc/swaps field 0
        # included, so it must be undone here exactly as read_mounts does for
        # mountinfo - otherwise a swap on a path carrying one of those bytes
        # never resolves to its device node.
        swap_path = _unescape(fields[0])
        dev = _device_number_of(swap_path)
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
    """Read the udev database's filesystem type and label for each device.

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
    """The filesystem type and label a udev database entry carries, if any."""
    text = _read_short_text(path, limit=MAX_ATTRIBUTE_CHARS)
    if text is None:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("E:"):
            continue
        key, _, value = line[2:].partition("=")
        name = _SIGNATURE_KEYS.get(key)
        if name is not None:
            found[name] = value
    return found


def _holder_names(node: Path) -> list[str]:
    """The sorted names of what sits directly on a block device."""
    holders = node / "holders"
    try:
        return sorted(child.name for child in holders.iterdir())
    except OSError:
        return []


def _read_short_text(path: Path, *, limit: int = MAX_ATTRIBUTE_CHARS) -> str | None:
    """Read one short sysfs attribute's stripped text, or ``None``.

    Args:
        path: The attribute to read.
        limit: The most characters it may carry, in text mode. See
            :data:`MAX_ATTRIBUTE_CHARS`.
    """
    try:
        with path.open("r", errors="replace") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return None
    if len(raw) > limit:
        return None
    return raw.strip()


__all__ = [
    "MAX_ATTRIBUTE_CHARS",
    "MAX_MOUNTINFO_BYTES",
    "read_mounts",
    "read_partitions",
    "read_signatures",
    "read_stacked",
    "read_swaps",
]
