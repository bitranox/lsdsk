# ADR 0007: Drives Inside a Storage Spaces Pool

**Status:** Accepted

## Context

Windows takes a drive out of the ordinary disk list when it joins a Storage Spaces pool. The
drive's device interface moves from `GUID_DEVINTERFACE_DISK` to `GUID_DEVINTERFACE_HIDDEN_DISK`,
which the SDK header `ntddstor.h` describes as disks "not reported through conventional APIs".
A scan that enumerates only the first class therefore lists the Space (a virtual disk on bus type
`Spaces`) and none of the drives it is built from. Until this decision the Space carried one
`pool-members` refusal saying the pool's drives went unread, and the scan reported itself
incomplete, because it was.

## Decision

1. **The reader enumerates both interface classes.** Every interface of
   `GUID_DEVINTERFACE_HIDDEN_DISK` is read exactly like a disk - opened, asked for IDENTIFY and
   SMART - after the ordinary disk class. A hidden interface whose drive number an ordinary
   interface already carries is not listed a second time but folded into the record kept for that
   drive, which takes the hidden mark and the removal relations from it, and its instance
   identifier where the kept record has none. So a drive publishing both is listed once and keeps
   the link to its Space, which only the hidden interface read.
2. **The capture records what Windows published, and the builder decides what it means.** Each
   disk entry carries its own device instance identifier, whether it came from the hidden class,
   and its `DEVPKEY_Device_RemovalRelations` list. A Space's instance identifier is
   `STORAGE\DISK\{...}`, and each pooled drive names it among its removal relations.
3. **A drive is linked to a Space when one of its removal relations names the Space's instance
   identifier, compared without regard to case.** Windows compares them that way, and the two
   spellings differ: the Space names itself `STORAGE\DISK\{...}` with an upper-case GUID, while
   its drives name it `STORAGE\Disk\{...}` with a lower-case one, measured on one guest. No
   WMI and no Storage Management API is consulted: the device tree already holds the relation.
4. **What uses a linked drive is the Space.** A new use kind, `space`, named by the Space's node
   (`PhysicalDrive4`) with the Space's volume paths as its mounts, the shape an LVM member already
   has. Without it a pooled drive would read `not mounted`, since no volume names it directly, and
   invite somebody to reuse a drive the pool depends on. A pool can hold several Spaces spread over
   the same drives, so a drive whose removal relations name several gets one `space` use per
   Space, each with that Space's mounts, listed once each in drive order rather than in the order
   Windows lists the relations.
5. **A Space with at least one linked drive no longer carries the `pool-members` refusal; a Space
   with none keeps it.** A capture taken before these fields existed has no link and so replays
   exactly as before. This reverses the 2026-10-19 choice to keep every Spaces scan incomplete,
   which was made because the drives could not be read; they now can.

## What was measured

On a Windows 11 virtual machine (QEMU, an AHCI controller, three SATA drives in one Mirror Space):
the three pooled drives were present and started; each was listed only under
`GUID_DEVINTERFACE_HIDDEN_DISK`; each opened read-write and answered ATA IDENTIFY, SMART READ
DATA and a SAT IDENTIFY through `IOCTL_SCSI_PASS_THROUGH` exactly as before pooling; and each
one's removal relations named `STORAGE\Disk\{...}` of the Space. The constant's name and value
were checked against the SDK header.

## Not measured

Simple and Parity layouts, several Spaces in one pool (the one-use-per-Space rule is held by tests
over the committed capture with a second Space planted, not by a machine that has one), a pool
with no virtual disk, a degraded pool, and any physical machine. A drive missing from a pool is pool health, which this tool does
not read; it is not a reading the scan left out.

## Left out

Listing a Space's member drives in the filesystems section. That section names the disk a volume
sits on, which for a Space is the Space; showing what the Space is built from is a separate view.

## Consequences

- A Spaces machine's scan is complete when every pooled drive answered, and each pooled drive is
  graded by the same rules as any other drive.
- The `kind` of a use in a disk's JSON `usage` gains the value `space`.
- The Windows capture gains three disk fields, all optional; older captures stay replayable.
