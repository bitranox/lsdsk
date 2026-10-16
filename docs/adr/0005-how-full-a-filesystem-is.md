# ADR 0005: How Full a Filesystem Is

**Status:** Accepted

## Context

The disk table says what a drive is, how well it is connected and what uses it, and nothing about
how much of it is left. "Also show how full the disk is" asked for the df answer, and inode usage
was added to it on the same day. A disk is not the unit that fills up: a filesystem is, and a disk
can hold several, one can span several disks, and a ZFS pool is neither.

Measured on two Proxmox hosts before deciding:

- `statvfs` on a ZFS dataset reports that dataset's own data plus the POOL's free space. Four
  datasets of one pool read 10%, 2%, 20% and 0% with identical free space.
- `statvfs` on each pool's root dataset read 0.0% on all seven pools, including one that
  `zpool list` showed at 75%: the root dataset's figure leaves out its descendants.
- What `statvfs` does give exactly on ZFS is the free space, within a few hundred kilobytes of
  `zfs list -o avail`.
- ZFS inode figures are meaningless: `f_files` and `f_ffree` come out of the free space and read
  about 0% of inodes used on every dataset.
- `/dev/zfs` is mode 0666, so the pool-statistics ioctl answers an unprivileged caller, and the
  node is absent inside an LXC container.

## Decision

**A section of its own, not a column.** A `filesystems` section and command, `lsdsk filesystems`,
follows the disks table on the printed default page and is page 9 of the interactive view. A
percentage per use in the `used by` cell and one percentage per disk were rejected: neither names
the thing that is full. `CliCommand` gains the member at the END, so keys `1` to `8` keep their
meaning.

**What is a row.** One row per filesystem that lives on an inventoried disk: a mounted block
filesystem, one row per ZFS POOL whatever its datasets (the rule ADR 0004 set for `used by`), and a
Windows volume that has a path. Bind mounts and btrfs subvolumes of one filesystem collapse into
one row by DEVICE NUMBER, showing the first mountpoint and `+N` for the others; filtering
mountinfo's root field instead would hide a btrfs `/@` root. tmpfs, NFS, FUSE, autofs and swap are
not filesystems on a disk and are never stat-ed, because only a mount whose device resolves to an
inventoried disk is asked. The disks of a row come from the same resolution as `used by`, so the
two sections cannot disagree about which disk holds a mount.

**A covered mount is not read.** `statvfs(path)` resolves the path to the TOPMOST mount there, so a
filesystem mounted over, by a second disk or by a tmpfs or NFS mount the reader drops, would be
given the size and fill of the one on top, and could raise a finding about the wrong filesystem.
The reader therefore marks, over EVERY mountinfo row before any is dropped, the mounts path lookup
cannot reach, from mountinfo's mount IDs, parent IDs and mountpoints alone. Lookup walks a
mountpoint's directories inside its parent mount and crosses into whatever is mounted on one of
them. So a mount is cut off when another mount with the same parent sits on a directory strictly
above its mountpoint, compared component by component (`/x/a` is above `/x/a/b` and not above
`/x/ab`). That holds whichever was made first, because a deeper mount made AFTER the one above it
is made inside it and records it as its parent; this was checked against the kernel in a user and
mount namespace. A mount stacked on its parent's own mountpoint is such a sibling, so the parent's
other children are cut off too, and the parent itself is covered. A mount is unreachable when it is
cut off or its parent is unreachable, and hidden when it is unreachable or covered. The process root
is the exception: lookup starts at it rather than crossing into it, so a mount stacked on `/` is
the unreachable one and hides nothing beneath the root. Comparing `st_dev` was rejected because a
btrfs subvolume reports an anonymous one that differs from mountinfo's. Such a hidden row carries
`hidden` in the capture and is never asked; a filesystem reachable at another mountpoint is read
and named there, and one reachable nowhere keeps its row with no figures and a `statvfs` refusal
saying why, rather than another filesystem's figures. A row with no percentage read earns no
severity marker, so a covered row never wears the marker of the mount on top, which carries the
same name. A capture taken before `hidden` existed reads every mount as reachable, as it did then.

**Fill is computed the way `df` computes it.** Used percent is used divided by used plus the space
free to an ordinary user, rounded up in exact integers. Ext4's reserved blocks therefore count as
unavailable, and lsdsk reads the same figure as `df` on the same mount. A ZFS pool is the exception
and rounds down, as `zpool list` does in its `cap` column: `df` reports a dataset, never a pool, so
its rule does not reach one, and a pool then reads the same figure as the tool ZFS users already
read. The domain therefore carries two rounding rules, keyed on the row's kind and never on its
name or filesystem type.

**Why a pool is read from ZFS, not from `statvfs`.** The measurements above rule `statvfs` out for
size and used on a pool. The reader opens `/dev/zfs` read-only, lists the pools with
`ZFS_IOC_POOL_CONFIGS` (`0x5A04`), and asks each for `ZFS_IOC_POOL_STATS` (`0x5A05`), taking the
size and allocation from the root vdev's `vdev_stats` array, which are the figures `zpool list`
prints. Both requests are passed bare, not through `_IOWR`, because Linux hands the number straight
through. Checked against the OpenZFS 2.4.4 source: the first has policy `zfs_secpolicy_none`, the
second `zfs_secpolicy_read`, which asks only that the caller is in the global zone or the pool is
visible, so neither needs a capability. A stand-alone probe run as `nobody` and as root gave
byte-identical answers equal to `zpool list -Hp` size and alloc on all four pools of a node.

**The ioctl's policies.** The command buffer is a zeroed 32 KiB, larger than `zfs_cmd_t`, which the
kernel copies in and out whole; the reader needs only the pool name at offset 0 and the destination
pointer and size at 4112 and 4120. A reply too large for the destination fails `ENOMEM` with the
needed size written back, and the reader grows the destination to it, within a bound, so a kernel
answer cannot size the allocation. The reply is the kernel's native little-endian nvlist, decoded
by a pure decoder (`adapters/hw/decode/nvlist.py`) tested on hand-built buffers: truncated input, a
wrong header, a missing or short stats array, nesting past the bound and an oversized string each
refuse with a message.

**The capture never holds a pool's configuration.** The config an answer carries names every vdev
by path, often a by-id name embedding a drive serial, and also the host name and GUIDs. It is
decoded at once and reduced: the capture keeps the pool name, its size, its allocation, the pool's
GUID and, for each leaf vdev, its type and the device number its path resolves to - never the path,
the host name or a vdev GUID - so a recapture cannot put a serial in a place the fixture scrub does
not know about.

**Absent is not refused.** A missing `/dev/zfs` (no node, or a node with no driver behind it) is no
refusal, the line the AHCI reader draws for a controller with no BAR5: a pool row exists only for a
pool the kernel lists, so with no listing there is no pool to miss, and a member label on a disk -
which may be a VM guest's pool on a host volume - is no evidence of one. A listing the control
device refused while it exists is a refusal of the reading `zfs-pools`, reported under the subject
`filesystems`, and draws no row. A listed pool's members come from its own configuration: each leaf
vdev's device number is traced to its disk through the same holder closure the used-by column
follows, so a pool on dm-crypt, LVM or md reaches the disks beneath it. A member the configuration
could not follow falls back to the udev labels, matched by the pool's GUID and never by its name. A
pool whose members are all files or virtual devices (loop, zram) has no row and no refusal, like any
other filesystem on no inventoried disk; a listed pool that a reading which was not taken could have
placed is a refusal of the reading `zfs-pool-disks` and draws no row. A capture taken before the
configuration was recorded traces members by label name, as it did then. A refused
`statvfs` is recorded against its filesystem under `statvfs`, a Windows volume's under
`volume-space` (the space query) or `volume-info` (the filesystem-information query), and each
becomes a `skipped` line, so `ok` is `false` on a scan that missed one. A container, or a capture
whose mountinfo was not read, carries `filesystems: null`, which means not read; an empty list
means read and none found.

**A capture from before this section is not read, not read as blank.** The builder decides from the
capture's own keys, never from a version string. The Linux reader writes `filesystem_space`
whenever it read mountinfo, so a capture without it is from before the reading; the Windows reader
writes `volume_space_read`, and a capture without that flag is from before the reading when no
volume with a path carries the figures or the reason one of its two calls was refused; with the flag,
the reading ran even where no volume is left to be a row. Either replays
as `filesystems: null` with no refusal, the same `ok` as before the section existed. Read as an
empty reading instead, it drew a row of `-` for every mount, pool and volume and claimed the ZFS
control device was missing on a host that was never asked.

**Windows.** `GetDiskFreeSpaceExW` and `GetVolumeInformationW` are asked on each volume's first
mount path, drive-letter roots before folder mounts, over the volumes `used by` already enumerates.
Measured unelevated through a limited-run-level scheduled task, both answer. A volume with no mount
path, such as the EFI system and recovery partitions, has nothing to ask and is not a row. A row's
size, used and free are the figures `GetDiskFreeSpaceExW` reports to the caller (its total and its
free-to-caller bytes, used being the one less the other), so all three share one scope: under a
per-user quota that is the quota, and the volume-wide free figure is recorded but not drawn; the
free-space figures of ADR 0006 are the whole volume's, so the row carries `space_is_quota` and the
detail panel says so. A volume whose paths could not be read, or one with a path that would not open
or report its disk extents, is a refusal of the run-wide reading `volume`, named by its drive letter
or, without one, by its place in the enumeration and the disks it reached. A disk whose number
Windows would not report is the refused reading `device-number`. An optical drive, a RAM disk and a
hidden volume with no path stay silent here; an EFI system partition whose extents failed still
leaves its disk's used-by cell undecided.

**The inode rule.** An inode count is a ceiling only where the table is made at format time, which
the builder decides from the filesystem TYPE and never from the numbers, since a ZFS `f_files`
looks plausible and means nothing. Ext2, ext3 and ext4 are fixed; xfs, btrfs, ZFS and NTFS grow
their metadata on demand. For those the table draws `n/a` and no finding is possible, and `-`
stays reserved for a figure nobody read; the legend names each marker only when the table drew it.

**Findings.** Space and inodes each have a hint and a warning limit, configured in `[thresholds]`:
`filesystem_full_hint_percent` 85, `filesystem_full_warning_percent` 95, `inodes_full_hint_percent`
85 and `inodes_full_warning_percent` 95. The defaults are starting values, not measured. A finding
fires AT its limit and its title says "at or above"; a warning makes the run exit 1 and a hint does
not. The four keys take a whole number from 1 to 100 and anything else falls back to the shipped
value with a warning, because a limit above 100 could never be reached and one at zero would fire
on everything.

**A read-only filesystem is listed and judged by nothing.** squashfs, iso9660 and anything mounted
`ro` are full by design, and a finding on them would be one the reader can do nothing about. A
filesystem with several mounts counts as read-only only when every mount is.

**The key move.** Every page is bound as `N,fN`, so page 9 takes `f9`, which was the rescan key.
Settled with the user: page 9 is `9,f9`, rescan moves to `r,f10`, and quit keeps `q` and `escape`
and gives up `f10`.

Left out: fill trends over time, LVM unallocated space, reporting a quota as a quota (a Windows
row follows one as the caller sees it, nothing more) and ZFS-specific thresholds.
How scattered the free space is was added afterwards and is decided in
[ADR 0006](0006-how-scattered-free-space-is.md).

## Consequences

Captures gain optional fields (`SCHEMA_VERSION` stays 2), so every committed fixture still parses
and every one older than the section exercises the not-read arm, where the section says the
filesystems were not read. The populated arm is a new capture, `linux-zfs-filesystems`, from a
Proxmox node: two imported ZFS pools, and mounts that are all ZFS datasets, so its
`filesystem_space` is the empty map. The serial invariant runs over it, and a pool's configuration
is absent from it by construction.

The Linux `statvfs` arm is therefore covered by hand-built captures only. No committed capture
carries a mount that was stat-ed, and no end-to-end host exercises it, since every reachable node
boots from ZFS and leaves its EFI system partition unmounted. A capture of a machine with an ext4 or
xfs filesystem on a listed disk, and an end-to-end host that has one, are still needed.

`f10` no longer quits; it rescans. A machine with a nearly full
filesystem that used to exit 0 can now exit 1, which is the point of the section and the reason the
limits are keys.

Fill is a snapshot. A trend over time is not attempted, and a filesystem that fills between two
runs shows as full only on the second.
