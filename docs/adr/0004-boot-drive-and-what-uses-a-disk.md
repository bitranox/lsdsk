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
devices stacked on it, named by `dm/name` or the md node. The whole disk is followed as well as its
partitions, so a holder on the raw device (a multipath path, a whole-disk md member) is found even
under a leftover partition table, and a stacked device's own partitions are followed too, which is
how a partitioned md array (IMSM or DDF fake RAID) reaches its mount. ZFS is the case those
cannot reach: `/` is a dataset, its `maj:min` is anonymous, and sysfs lists no holder for a pool
member. The udev database (`/run/udev/data/b<maj>:<min>`, world-readable) records
`ID_FS_TYPE=zfs_member` and the pool name in `ID_FS_LABEL` for each member partition, and the
mount's source names the pool (`rpool/ROOT/...`), which joins the two. The capture keeps only what
this resolution reads: a mount with no block device behind it (tmpfs, NFS, FUSE), any source other
than a ZFS pool name, and a mapping UUID past its type prefix (`LVM-`, `CRYPT-`) are left out.

**Partition evidence beats a whole-disk signature.** ZFS keeps two of its four labels at the END
of a vdev, so a pool partition that runs to the end of its disk makes a probe of the WHOLE disk
find a pool member too; leftover RAID signatures do the same. A whole-disk signature is used only
when no partition of that disk carries one.

**Windows sources.** Volume enumeration (`FindFirstVolumeW`), each volume's mount paths
(`GetVolumePathNamesForVolumeNameW`: letters and folder mounts), and
`IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`, whose disk numbers are the `PhysicalDrive` numbers the
reader already records. A spanned volume names several disks. A BitLocker-locked volume still
maps; a volume with no disk behind it (a RAM disk) maps to none. The reader also records what
`GetDriveTypeW` answers for each volume's first path, so a failure on an optical drive or a RAM
disk, or on a volume with no path at all, is told apart from one on a fixed volume.

**Boot.** A disk is marked `boot` when it holds the running system or a boot partition: on Linux
every disk under `/` plus any disk holding a MOUNTED `/boot`, `/boot/efi` or `/efi` (systemd's
recommended mountpoint for the EFI system partition); on Windows the disk holding the Windows
directory plus the disk holding the EFI system partition, found by its GPT type. A mirror marks
every member. A container whose `/` sits on no listed disk marks nothing,
because the host's disks are not that system's boot drive. The firmware's `BootCurrent` entry was
rejected: it needs privilege, has no answer on a BIOS boot, and names a partition that must still
be mapped.

**What a disk shows.** One short answer per disk, the same shape on both platforms: a mount
(`/home`), a letter (`C:`), `swap`, or the stack it belongs to with that stack's few mounts
(`zfs:rpool`, `lvm:vg0 -> /var`, `md:md0 -> /srv`). ZFS shows the POOL, never its datasets, whose
count is unbounded. The boot mark leads the cell (`boot zfs:rpool`, `boot C:`).

**Where.** A `used by` column at the end of the disks table, printed and interactive alike,
dropped before any other column on a narrow page and flexible and clipped with a marked cut like
`model`, with no configuration key: a key no committed capture can move would fail the live-key
harness, and nothing in this cell grows without bound the way an NVMe WWN does, plus the full text
in the detail panel and a `usage` field in the JSON payload.

**Three states, never two.** `-` is a reading not taken - a capture older than these fields, or a
source absent or refused - and is named in the legend; `not mounted` is a reading taken that
found nothing in use; anything else is content. A capture without the fields renders `-`, never
`not mounted`, because absence of a reading is not a reading of absence. So a disk that resolved
nothing reads `-` when any source that could have named it went unread: on Linux the udev
database, the swap list, the disk's or a partition's device number, or its partition list; on
Windows a volume whose paths could not be read, or one with a path that failed to open or to name
its disk, unless it is an optical drive or a RAM disk. A Linux disk carrying a btrfs signature
that resolved nothing reads `-` too: mountinfo names only one member of a multi-device btrfs
filesystem, and joining the others would mean recording the filesystem's UUID. `not mounted`
itself does not mean a disk is safe to wipe: a disk passed through to a virtual machine, a Storage
Spaces member or an exported ZFS pool shows it too.

## Consequences

Captures gain optional fields, so every committed fixture still parses and exercises the
not-read arm; the resolving logic is tested with hand-built captures, and the real-hardware run
asserts that the disk under `/` carries `boot` on every host it visits. Whether an unelevated
Windows session may open a volume handle is measured before the reader is written; if it may not,
the refusal is recorded like every other refused reading and the run is not `ok`.

On a virtual Windows 11 machine, extending the volume-enumeration probe with
IOCTL_DISK_GET_PARTITION_INFO_EX on the same zero-access volume handle used for
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS confirmed the FAT32 "SYSTEM" volume reports GPT partition
style (PartitionStyle = 1) with PartitionType GUID C12A7328-F81F-11D2-BA4B-00A0C93EC93B, the
defined EFI System Partition type, both run elevated and run unelevated through a LIMITED-run-level
scheduled task (confirmed unelevated via shell32.IsUserAnAdmin() returning false inside the task).
On this machine every volume handle opened successfully and every IOCTL_DISK_GET_PARTITION_INFO_EX
call succeeded in BOTH the elevated and the unelevated run: no volume-level open or partition-info
read needed administrator rights. On a physical Windows machine, the same elevated probe read
partition style and type for every volume including the boot disk's FAT32 ESP (same GUID, is_esp
true) and its data volumes (GPT "Basic data partition" and "Microsoft reserved partition" types);
the one volume that failed to open even elevated (error 5, access denied) was an unrelated,
separately-secured volume on that machine, and a BitLocker-locked volume on an MBR disk (its
filesystem query failed with 0x80310000, FVE_E_LOCKED_VOLUME) still opened, mapped to its disk,
and reported PartitionStyle 0, which is PARTITION_STYLE_MBR and correctly not an EFI system
partition. So an unelevated process can open every ordinary volume, and the partition query
succeeds on every handle that opens; neither reading needs to be recorded as refused.

`GetDriveTypeW` on that separately-secured volume's letter answers `DRIVE_FIXED` (its GUID path
answers `DRIVE_NO_ROOT_DIR`, which is why the reader asks the first path), so its failure keeps
blocking `not mounted` on that machine. Neither measured host has an optical drive or a RAM disk,
so that those fail the extents query is taken from the API's semantics, not measured.
`GetVolumePathNamesForVolumeNameW` offered a one-character buffer failed with `ERROR_MORE_DATA` and
reported the length it needed on both hosts, which the reader's one retry relies on.

A real-hardware run against four ZFS-root Proxmox nodes, a Proxmox Backup Server node and a
physical Windows machine found a boot disk on every reachable host: on each ZFS-root host, exactly
the two members of its root mirror carried `boot`, each reporting `zfs:rpool`, and the Windows
host's single boot disk reported `boot` with its system drive letter.
