# ADR 0006: How Scattered Free Space Is

**Status:** Accepted

## Context

The filesystems section says how full a filesystem is (ADR 0005) and nothing about how its free
space is laid out. "Also show the fragmentation of the filesystems" asked for the answer a
ZFS user already reads as `FRAG` in `zpool list`: not how much is free but how broken up it is, so
that a pool at 70% with a high score is known to be harder to write into than one at 70% with a low
one.

Two things the word can mean were separated first:

- **File fragmentation**: how many pieces each file is stored in. Answering it means reading the
  extent list of every file on the filesystem.
- **Free-space fragmentation**: how many pieces the unused space is in, and how small they are.
  Answering it needs the free extents of the filesystem and nothing about its files.

## Decision

**Free-space fragmentation, shown only.** The figure is a column and a panel group. No finding is
raised for it and no `[thresholds]` key exists for it, so a scattered filesystem never changes the
exit code (user: "Shown only").

**One score, ZFS's own.** The score table is OpenZFS `zfs-2.4.0` `module/zfs/metaslab.c`
`zfs_frag_table`, copied verbatim and tagged with that source in the code: 100, 99, 97, 93, 88, 83,
77, 71, 64, 57, 50, 43, 36, 29, 23, 17, 12, 7, 3, 1, 0, from 512 B up to 512 MiB. The table was
fetched and read on 2026-10-13. OpenZFS 2.4.4, which the hosts used for the measurements below run,
carries it; 2.2.x carried an older sixteen-entry one. Using ZFS's table for every filesystem means
one figure reads the same way everywhere and a pool's score is the one `zpool list` prints.

**21 buckets of free bytes.** Bucket `i` holds free extents of `[512 * 2**i, 1024 * 2**i)` bytes.
Bucket 0 also takes anything under 512 B and bucket 20 takes everything from 512 MiB up. Each bucket
is weighted by the free bytes it actually holds. ZFS weights a bucket by its lower bound times the
count, which counts less than the bytes in the upper part of a bucket. That only matters where lsdsk
computes the score itself (ext4, xfs, Windows volumes); a pool's figure is ZFS's own and is not
recomputed. How far the two weightings differ on histograms of real extents was not measured.

**Which filesystems have a figure.**

- A ZFS pool: the `fragmentation` property through `ZFS_IOC_POOL_GET_PROPS`, on the same read-only
  `/dev/zfs` handle that reads size and allocation. A pool can publish no figure (`ZFS_FRAG_INVALID`,
  any value above 100, which `zpool list` prints as `-`); the reader records that as absent and the
  cell draws `n/a`. A pool carries a score, no extent figures and an empty histogram, because ZFS
  does not publish its free-space histogram to a caller.
- ext4 and xfs: the free extents of the mounted filesystem through `FS_IOC_GETFSMAP`.
- A Windows volume: the volume bitmap through `FSCTL_GET_VOLUME_BITMAP`, folded into free runs. This
  needs an elevated session and opens the volume by its GUID path.
- A Linux mount of any other type (btrfs, vfat, tmpfs, and the types with no free-space source this
  tool reads) draws `n/a`. An elevated Windows run walks every volume whatever its type; an
  unelevated one walks none.

**Two bounds, not settings.** A read stops at `MAX_FREE_EXTENTS = 1_000_000` free extents (or free
runs on Windows) per filesystem and refuses with a reason naming the number, because a very
fragmented large filesystem would otherwise hold the run for as long as it takes to walk it and
allocate as many records. The Linux walk has a second bound, `MAX_FSMAP_RECORDS = 4_000_000`
space-map records of any kind, and refuses with a reason naming that number. It exists because
`FS_IOC_GETFSMAP` returns every record of the filesystem's space map, not only the free ones: a
filesystem with reverse mapping, such as xfs, lists the extent of every file as well, so the free
extents alone would not bound the walk. Both are module constants and not configuration keys: a
limit a user can raise is a limit a typo can turn into an unbounded read, and the bounds exist to
protect the run, not to express a preference.

**The refusals, by name.** A reading the system refused is recorded and becomes a `skipped` line,
like every other refusal here: `zfs-fragmentation` (the pool listing, inventory level),
`free-extents` (a Linux mount row), `volume-bitmap` (a Windows volume row) and `fragmentation` (the
run-wide line a Windows run without Administrator carries in the JSON envelope's `skipped`,
`fragmentation: filesystems - needs Administrator`). The key is a refusal that was recorded, never a
field being absent.

**The views name an inventory-level refusal.** The `zfs-pools`, `zfs-fragmentation` and
`fragmentation` refusals belong to no row, so a row cannot carry them. The printed filesystems view,
the same section on the default page and the interactive filesystems page each add one sentence under
the table, `Not read: fragmentation (needs Administrator).`, naming every refusal in
`inventory.unread_filesystems` with its reason. It is keyed on that record and never on the dashes the
table drew, because a dash is also an ordinary unread cell. Without it an unelevated Windows run
showed `-` in every `frag` cell and named no cause anywhere but the JSON.

**`n/a` and `-` are different claims.** `n/a` in the `frag` cell means no figure can exist: the type
has no free-space source, the pool publishes none, or there is no free space to score. `-` means a
figure could exist and was not read. The legend names each only when the table drew it, and `n/a`
carries a per-column meaning there, because the inodes column draws it too.

**Shape of the output.** The table column is `frag`, right-aligned, and drops first as the terminal
narrows (priority 7, so it drops before `inodes%`). The panel gains a `free space` group with
`fragmentation`, `free extents`, `largest` and `average`. The JSON entry gains `free_space`: `null`, or
`{fragmentation_percent, free_extent_count, free_extent_bytes, largest_free_extent_bytes,
histogram}`, and `histogram` is the 21 buckets of free bytes. A filesystem with no free extent at all
scores no figure and has no average free run, so its `fragmentation` and `average` draw `n/a` in the
panel.

## What was measured

All of it on 2026-10-13 with this change.

- **Pool fragmentation as an ordinary user.** `ZFS_IOC_POOL_GET_PROPS` on a read-only `/dev/zfs`
  handle, run unprivileged on OpenZFS 2.4.4, answered on every pool tried. On the real-hardware run,
  13 of 13 pools on five ZFS hosts read the same figure as `zpool list`, with values from 2 to 41.
- **Windows, elevated.** One `lsdsk filesystems --format json` run on a Windows 11 VM took 2.9 s warm
  and 5.4 s cold with a 499 GiB NTFS system volume 13% full (41,897 free extents, score 1), read
  through the volume GUID path. A temporary 4 GiB NTFS volume filled to 95% read score 3 in 2.9 s.
- **Windows, not elevated.** A probe found that opening the volume fails with error 5, which is why
  the run does not attempt it: every `frag` cell draws `-`, the filesystems views say
  `Not read: fragmentation (needs Administrator).` under the table, and the JSON envelope's `skipped`
  carries the `fragmentation: filesystems - needs Administrator` line.
- **Cost on a large, nearly full volume is not measured end to end.** The bitmap expansion runs about
  1.8 s per TiB of all-used clusters in a unit benchmark; no such volume was read through the whole
  path.
- **ReFS was not run.** The Windows edition available would not format a ReFS volume, so neither
  outcome is claimed.

## What was only read from source

- `FS_IOC_GETFSMAP` on ext4 and xfs was read from kernel source and never run on hardware here.
- That an unprivileged caller may use it on xfs is from the same reading.
- That a reflinked xfs filesystem lists a shared block as several records, so a free-space walk must
  not assume every record is distinct, is from the same reading.

The Linux free-extent path is therefore covered by hand-built captures and unit tests; a capture of an
ext4 or xfs host and an end-to-end host that has one remain needed, as they already were for the
`statvfs` path in ADR 0005.

## Left out

File fragmentation. It needs the extent list of every file, which is a walk over the whole
filesystem and not a reading, and the only figure Windows publishes for it comes from the
defragmenter, which would mean running a subprocess. This tool runs none.

## Consequences

Captures gain optional fields (`SCHEMA_VERSION` stays 2): a pool's fragmentation, a mount's free
extents, a Windows volume's `free_extents` entry and a Windows run's `volume_bitmaps_read`, so every
committed fixture still parses and an older one replays with no figure. The `frag` cell of a row with
no reading draws `-`, which the three legend tests written before the column existed had to be told
about; their fixtures now carry a `FreeSpace` so they still test what they were written for.

Fragmentation is a snapshot of the moment of the run. It is not trended and does not feed a finding.
