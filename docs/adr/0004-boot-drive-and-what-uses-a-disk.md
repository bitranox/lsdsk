# ADR 0004: The Boot Drive, and What Uses a Disk

**Status:** Accepted

## Context

The disk table says what a drive is and how well it is connected, and nothing about what the
machine does with it. Before pulling, replacing or reformatting a drive, the first question is
whether the system boots from it, and the second is which drive letter or mountpoint it is. A
reader answering those elsewhere (`lsblk`, Disk Management) has to match devices by serial across
two tools.

The answer must come without a subprocess and, on Linux, without root, like every other reading.
Measured on two Proxmox hosts with a ZFS root mirror, a container, a virtual Windows machine and a
physical one, the sources below give it on both platforms.

## Decision

**Linux sources.** `/proc/self/mountinfo` gives every mount by `maj:min`; `/proc/swaps` the active
swap. Each partition's `holders/` chain, followed transitively, reaches the LVM, md and dm-crypt
devices stacked on it, named by `dm/name` or the md node. ZFS is the case those cannot reach: `/`
is a dataset, its `maj:min` is anonymous, and sysfs lists no holder for a pool member. The udev
database (`/run/udev/data/b<maj>:<min>`, world-readable) records `ID_FS_TYPE=zfs_member` and the
pool name in `ID_FS_LABEL` for each member partition, and the mount's source names the pool
(`rpool/ROOT/...`), which joins the two.

**Partition evidence beats a whole-disk signature.** ZFS keeps two of its four labels at the END
of a vdev, so a pool partition that runs to the end of its disk makes a probe of the WHOLE disk
find a pool member too; leftover RAID signatures do the same. A whole-disk signature is used only
when no partition of that disk carries one.

**Windows sources.** Volume enumeration (`FindFirstVolumeW`), each volume's mount paths
(`GetVolumePathNamesForVolumeNameW`: letters and folder mounts), and
`IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`, whose disk numbers are the `PhysicalDrive` numbers the
reader already records. A spanned volume names several disks. A BitLocker-locked volume still
maps; a volume with no disk behind it (a RAM disk) maps to none.

**Boot.** A disk is marked `boot` when it holds the running system or a boot partition: on Linux
every disk under `/` plus any disk holding a MOUNTED `/boot` or `/boot/efi`; on Windows the disk
holding the Windows directory plus the disk holding the EFI system partition, found by its GPT
type. A mirror marks every member. A container whose `/` sits on no listed disk marks nothing,
because the host's disks are not that system's boot drive. The firmware's `BootCurrent` entry was
rejected: it needs privilege, has no answer on a BIOS boot, and names a partition that must still
be mapped.

**What a disk shows.** One short answer per disk, the same shape on both platforms: a mount
(`/home`), a letter (`C:`), `swap`, or the stack it belongs to with that stack's few mounts
(`zfs:rpool`, `lvm:vg0 -> /var`, `md0 -> /srv`). ZFS shows the POOL, never its datasets, whose
count is unbounded. The boot mark leads the cell (`boot zfs:rpool`, `boot C:`).

**Where.** A `used by` column at the end of the disks table, printed and interactive alike,
dropped before any other column on a narrow page and capped at `display.used_by_width` with a
marked cut, plus the full text in the detail panel and a `usage` field in the JSON payload.

**Three states, never two.** `-` is a reading not taken - a capture older than these fields, or a
source absent or refused - and is named in the legend; `not mounted` is a reading taken that
found nothing in use; anything else is content. A capture without the fields renders `-`, never
`not mounted`, because absence of a reading is not a reading of absence.

## Consequences

Captures gain optional fields, so every committed fixture still parses and exercises the
not-read arm; the resolving logic is tested with hand-built captures, and the real-hardware run
asserts that the disk under `/` carries `boot` on every host it visits. Whether an unelevated
Windows session may open a volume handle is measured before the reader is written; if it may not,
the refusal is recorded like every other refused reading and the run is not `ok`.
