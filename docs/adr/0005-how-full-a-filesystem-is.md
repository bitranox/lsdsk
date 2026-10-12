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

**Fill is computed the way `df` computes it.** Used percent is used divided by used plus the space
free to an ordinary user, rounded up in exact integers. Ext4's reserved blocks therefore count as
unavailable, and lsdsk reads the same figure as `df` on the same mount.

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
decoded at once and dropped: the capture keeps the pool name, its size and its allocation and
nothing else, so a recapture cannot put a serial in a place the fixture scrub does not know about.

**Absent is not refused.** A missing `/dev/zfs` (no node, or a node with no driver behind it) is no
refusal when no disk carries a ZFS member, the line the AHCI reader draws for a controller with no
BAR5. Absent while a disk does carry a pool member is a refusal under the subject `zfs-pools`: the
pool exists and could not be read. A refused `statvfs` is recorded against its filesystem under
`statvfs`, a Windows volume's under `volume-space`, and each becomes a `skipped` line, so `ok` is
`false` on a scan that missed one. A container, or a capture whose mountinfo was not read, carries
`filesystems: null`, which means not read; an empty list means read and none found.

**Windows.** `GetDiskFreeSpaceExW` and `GetVolumeInformationW` are asked on each volume's first
mount path, over the volumes `used by` already enumerates. Measured unelevated through a
limited-run-level scheduled task, both answer. A volume with no mount path, such as the EFI system
and recovery partitions, has nothing to ask and is not a row.

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

Left out: fill trends over time, LVM unallocated space, quotas and ZFS-specific thresholds.

## Consequences

Captures gain optional fields (`SCHEMA_VERSION` stays 2), so every committed fixture still parses
and exercises the not-read arm, where the section says the filesystems were not read. The
populated arm is a new capture, `linux-zfs-filesystems`, carrying ext4 mounts and ZFS pools. The
serial invariant runs over it, and a pool's configuration is absent from it by construction.

`f10` no longer quits; it rescans. A machine with a nearly full
filesystem that used to exit 0 can now exit 1, which is the point of the section and the reason the
limits are keys.

Fill is a snapshot. A trend over time is not attempted, and a filesystem that fills between two
runs shows as full only on the second.
