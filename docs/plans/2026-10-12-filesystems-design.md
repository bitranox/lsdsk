# Design: how full each filesystem is (backlog [35])

Status: validated with the user section by section, 2026-10-12. The implementation plan follows from
this document; ADR 0005 will record the decisions once the feasibility probes have run.

## The request

"Also show how full the disk is" (user, 2026-10-05). Inode usage was added to the request on
2026-10-12.

## Decisions taken while designing

| Question                          | Decision                                                                | Rejected                                                      |
|-----------------------------------|-------------------------------------------------------------------------|---------------------------------------------------------------|
| Where the fill level is shown     | a new df-style `filesystems` section and command                        | a percentage per use in `used by`; one percentage per disk    |
| How a ZFS pool's fill is read     | the `/dev/zfs` pool-stats ioctl, decoded by a pure, byte-tested decoder | free space only from statvfs; ZFS left out of a first release |
| Whether fullness raises a finding | yes, hint and warning thresholds for space and for inodes, configurable | display only                                                  |

## What was measured before deciding

- `statvfs` on a ZFS dataset reports that dataset's own data plus the POOL's free space: four
  datasets of one pool read 10%, 2%, 20% and 0% with identical free space.
- `statvfs` on each pool's ROOT dataset mount read 0.0% on all seven pools of two Proxmox nodes,
  including one `zpool list` showed at 75%: the root dataset's figure excludes its descendants.
- What `statvfs` does give exactly on ZFS is free space: `f_bavail` matched the root dataset's
  `zfs list -o avail` within a few hundred KB.
- The root dataset's `used / (used + avail)` tracked `zpool list` cap within about two points.
- `/dev/zfs` is mode 0666 on both nodes measured, and absent inside an LXC container.
- `/proc/spl/kstat/zfs/<pool>/` publishes state, iostats, txgs and objsets, and no capacity.
- ZFS inode figures are meaningless: `f_ffree` was identical on every dataset, derived from free
  space, so every dataset reads about 0% of inodes used.

## Feasibility probes (run 2026-10-12, both passed)

From the OpenZFS 2.4.4 source (the nodes run kernel modules 2.4.3 and 2.4.4):

- `ZFS_IOC_POOL_CONFIGS` is `0x5a04` with policy `zfs_secpolicy_none`; `ZFS_IOC_POOL_STATS` is
  `0x5a05` with `zfs_secpolicy_read`, which asks only that the caller is in the global zone or the
  pool is visible - no capability. Linux passes the request number straight through
  (`vecnum = cmd - ZFS_IOC_FIRST`) and copies `sizeof (zfs_cmd_t)` in and out.
- In `zfs_cmd_t` the reader needs only `zc_name` (offset 0, 4096 bytes) and the destination buffer
  pointer and size (offsets 4112 and 4120). A buffer that is too small fails `ENOMEM` with the
  needed size written back into `zc_nvlist_dst_size`.
- The kernel packs with `NV_ENCODE_NATIVE`: a 4-byte stream header (encoding 0, endian 1), the top
  list's version and flags, then pairs, each a 16-byte header (size, name length, reserved, element
  count, type), the name, and the value at the next 8-byte boundary. An embedded list or list array
  carries its `nvlist_t` header(s) inside the pair and its own pairs FOLLOW the pair in the stream,
  each list ending in four zero bytes.
- Size and alloc are the root vdev's `vdev_stats` array, elements 4 (`vs_space`) and 3
  (`vs_alloc`).

A stand-alone probe built on that layout, run on a node as `nobody` (euid 65534) and as root, gave
byte-identical answers, and both equalled `zpool list -Hp` size and alloc to the byte on all four
pools. So the unprivileged reading is exact.

One finding for backlog [40], not for this design: the root vdev's `vs_fragmentation` (element 27)
read 0 on every pool while `zpool list` reported FRAG 35, 2, 27 and 40; the pool's FRAG figure
comes from somewhere else (its pool properties) and needs its own probe.

Windows, on a virtual Windows 11 machine through a limited-run-level scheduled task
(`IsUserAnAdmin` false): `GetDiskFreeSpaceExW` and `GetVolumeInformationW` both answered on the
volume's mount path (NTFS, total, free and free-for-caller). The two volumes with no mount path
(the EFI system and recovery partitions) have nothing to ask, so they are not rows.

## 1. Scope and placement

One row per filesystem that lives on a disk in the inventory:

- a mounted block filesystem (ext4, xfs, btrfs, vfat, ...);
- one row per ZFS POOL, never per dataset (the ADR 0004 rule: a pool's datasets are unbounded);
- a Windows volume.

Excluded, because they are not on a disk or are not filesystems: tmpfs, NFS, FUSE, autofs, swap,
and an LVM volume group's unallocated extents. A filesystem on LVM, md or dm-crypt is a row, and
its disks come through the holders chain the `used by` resolution already follows.

One filesystem, one row: bind mounts and btrfs subvolumes collapse by DEVICE NUMBER, showing the
first mountpoint plus `+N`. A filter on mountinfo's root field would hide a btrfs `/@` root.

Columns: `filesystem` (mount, letter or pool), `type`, `size`, `used`, `free`, `use%`, `inodes%`
(`n/a` where the inode table is not fixed), `disks` (their nodes). Sizes are written in both scales
(`500GB/466GiB`) where there is room.

On the printed default page the section follows the disks table. In `CliCommand` the new member is
APPENDED, so it is TUI key `9` and keys `1` to `8` keep their meaning; the enum order and the page
order are already decoupled.

The JSON scan envelope gains a `filesystems` list, bytes as integers and every figure nullable. The
disks table and its `used by` cell are unchanged.

## 2. Sources and data flow

The reader, capture and builder split stays as it is, and every new capture field is optional, so
every committed fixture still parses and exercises the not-read arm.

- **Linux, per filesystem**: `os.statvfs` on one mountpoint per device number gives size,
  free-for-users (`f_bavail`) and inodes (`f_files`, `f_ffree`); the type comes from mountinfo. The
  capture records the raw statvfs numbers per device number.
- **Linux, ZFS pool**: open `/dev/zfs` read-only, enumerate the pools with the pool-configs ioctl,
  ask each for its stats, and take size, alloc and free from the root vdev's stats array - the
  figures `zpool list` prints.
- **Privacy**: the reader decodes the returned nvlist at once and the capture keeps only pool name,
  size, alloc and free, never the buffer. A pool configuration carries vdev paths (often by-id names
  that embed drive serials), the host name and GUIDs; recording it would put a serial in a place the
  fixture scrub does not know about. The decoder is still pure and tested on hand-built buffers.
- **Windows, per volume**: `GetDiskFreeSpaceExW` and `GetVolumeInformationW` (filesystem name) on
  each volume's first mount path, over the volumes the `used by` reader already enumerates.
- **Joining**: a filesystem's disks come from the same resolution as `used by`, so the two sections
  cannot disagree about which disk holds a mount.

## 3. Domain model, findings and thresholds

`Filesystem`, a frozen `DomainModel`: `kind` (a new `FilesystemKind`: `MOUNT`, `POOL`, `VOLUME`),
`name`, `other_mounts`, `fs_type`, `disks`, `size_bytes`, `used_bytes`, `free_bytes`,
`inodes_total`, `inodes_free`, each figure nullable.

- Used percent is `used / (used + free)` with free-for-users, the way `df` computes it, so ext4's
  root-reserved blocks count as unavailable and lsdsk agrees with `df` on the same mount.
- Whether the inode table is fixed is decided by the BUILDER from the filesystem type (ext2, ext3,
  ext4 fixed; everything else `n/a`), never from the numbers: a ZFS `f_files` looks plausible and
  means nothing.
- `Inventory.filesystems` is `tuple[Filesystem, ...] | None`: `None` is not read, an empty tuple is
  read and found nothing.

Findings, in `domain/diagnostics.py`: space and inodes each have a hint and a warning threshold. The
sentence says "at or above", because the rule fires at the threshold, and names the filesystem, its
disks and the figure. A warning makes the run exit 1; a hint does not.

New `[thresholds]` keys with shipped defaults (a starting choice, not measured):
`filesystem_full_hint_pct = 85`, `filesystem_full_warning_pct = 95`, `inodes_full_hint_pct = 85`,
`inodes_full_warning_pct = 95`.

A read-only mount (squashfs, iso9660, anything mounted `ro`) is listed but raises no finding: it is
full by design.

Left out: fill trends over time, LVM unallocated space, quotas, ZFS-specific thresholds.

## 4. Refusals, markers and rendering

- A refused reading is recorded as a `RefusedReading` with its subject (`statvfs` on a mount,
  `zfs-pools`, `volume-space`), becomes a `skipped` line and makes `ok` false.
- `/dev/zfs` ABSENT is not a refusal when no disk carries a ZFS member (a container, or ZFS not
  loaded) - the same line the AHCI reader draws for a controller with no BAR5. Absent while a disk
  does carry a pool member is a refusal: the pool exists and could not be read.
- Only mounts whose device resolves to an inventory disk are stat-ed, so NFS, FUSE and autofs are
  never touched. No threads or timeouts are added.
- Markers: `-` for a figure nobody read and `n/a` for inodes on a dynamic-table filesystem, each
  named in the section's legend only when drawn. A severity always carries its marker beside the
  colour.
- Layout through `Layout.preferring` and `layout.fit`; under width pressure the columns go in the
  order `inodes%`, `type`, `size`, `used`; `filesystem`, `use%` and `disks` stay, and `filesystem`
  is clipped with the marked cut.
- The TUI page derives its columns from the printed table's column list, uses the TUI palette, and
  fills through one method on both the mount and the rescan path.
- JSON carries `null` for an unread figure and an `inode_table` field (`fixed` or `dynamic`), so a
  consumer can tell `n/a` from unread.

## 5. Testing, fixtures and the order of work

1. Feasibility probes first, each of which can change the design: the OpenZFS headers and source
   for the ioctl numbers, `zfs_cmd_t` and the nvlist encoding, then a stand-alone probe run as an
   unprivileged user on a Proxmox node compared against `zpool list -Hp`; and the two Windows calls
   run unelevated through a limited-run-level scheduled task. A failure goes back to the user before
   that reader is written.
2. The nvlist decoder, test-first on hand-built buffers: the happy path, truncated input, a wrong
   header, a missing or short vdev-stats array, a nesting bomb and an oversized string, each refusal
   asserting its message.
3. Builders, on hand-built captures: device-number dedup of bind mounts and btrfs subvolumes, a
   filesystem on LVM over two disks, the read-only exclusion, absent versus refused `/dev/zfs`, an
   old capture rendering `-`.
4. One new scrubbed Linux capture carrying ext4 and a ZFS pool for the populated arm; the serial
   invariant test runs over it.
5. The guards that must stay green: the default page carries every section (now nine), the TUI key
   order against a written-out list ending in `filesystems`, the configuration-key guards, `__all__`
   completeness, the translation manifest for the twinned documents, and the shipped skill (changed
   only through the skill-writer).
6. Real hardware: on the integration hosts a pool row matches `zpool list` cap within one point and
   every host's `/` row is populated.
7. ADR 0005, then a 1.8.0 release (a new command and a new finding class).
