# What it finds, and how it decides

**English** | [Deutsch](de/FINDINGS.md)

Every rule lsdsk applies, what evidence it needs before it will call something a
fault, and what it refuses to guess. Back to the [README](README.md).

## What it finds

- A link negotiated below what both ends support, which is nearly always a
  cable, a backplane slot or a connector.
- Interface CRC errors, the frames corrupted in transit and resent. These are
  the cable, not the drive, and replacing the drive fixes nothing.
- The overall SMART verdict, computed the way the drive computes it: failing
  when a graded attribute has reached the threshold its maker set.
- Two drives in the wrong ports: a slow one holding a fast seat that a faster
  drive is waiting for.
- A controller in a slot narrower or slower than it needs, naming the free slot
  to move it to, or a card that would lose nothing by swapping places with it: a
  warning when the drives on it would use the faster slot, a hint when they fit
  the one it has.
- A controller capped by the mainboard, with the PCIe generation that would lift
  it and whether the attached drives can even use the difference.
- A drive held back by a slower port than it supports.
- Wear-out against configurable bands, and reallocated, pending and
  uncorrectable sectors and NVMe media errors on any count above zero.
- Temperature against the limits the drive itself publishes, not a guess.
- Identical models running mismatched firmware.
- Where there is room for another drive, and whether that controller has the
  bandwidth to feed it. A SAS HBA answers this exactly, because each phy is a
  real port the kernel publishes. An AHCI controller answers it only when its
  firmware publishes a ports-implemented bitmap, and otherwise reports no count
  at all rather than the inflated one the kernel's port list would give. An NVMe
  controller has no spare port to report: it is the drive's own interface. Where
  the count is absent, `lsdsk slots` still shows which PCIe ports are free.

Severity is graded by what the machine could actually give. A card running below
its own maximum in a board that has nothing faster is a dim hint, not a warning,
because there is nothing to fix.

## A number is not a rate

An error counter lives in the drive's own non-volatile table. It survives
reboots, power cycles and reinstalls, and the host cannot clear it. That is what
makes it trustworthy and also what makes it nearly useless on its own: it says
how much damage there has ever been, never when.

Two drives on one machine here make the point. Both are the same model, both
report hundreds of thousands of interface CRC errors. One gained sixteen
thousand of them in fifteen hours. The other has not gained one, and its own
lifetime rate says a couple of hundred should have appeared in that time. The
first is corrupting frames right now. The second is a cable somebody already
reseated, probably years ago.

Every tool that reads the total alone reports those two identically. lsdsk does
not, because it keeps its own record:

```
device        counter           total  change  span  per hour  verdict
/dev/sdc      interface CRC     99361     +16   16h       1.0  rising
/dev/sdd      interface CRC   2196127  +16642   15h      1109  rising
/dev/sde      interface CRC       430      +0   15h         -  too soon to say, this drive's rate would not have produced even one in 15h
/dev/sdj      interface CRC    462640      +0   16h         -  no new in 16h, 235 were due
```

`span` is the window the `change` figure covers, and it is measured per counter
rather than per drive: it reaches back to where that counter last moved, so one
drive legitimately shows a different figure on each of its rows.

Rates are per power-on hour of the drive itself rather than per hour of wall
clock. The drive's own clock is monotonic, ignores clock steps and timezones,
and does not advance while the machine is off, so the figure still means
something on a host that runs two weeks a year.

The last two rows are the part worth arguing about. Silence only counts as
evidence when the drive's own history says errors should have turned up: at 430
errors over ten thousand hours, fifteen quiet hours prove nothing and the tool
says so rather than implying the drive is fine. A fixed rule cannot do that, and
a fixed one-week rule refuses every drive on this machine including the one
whose fault is provably over.

The record feeds itself. Any ordinary run stores one reading when the drives'
clocks have moved on, so nothing has to be set up; `lsdsk record` from a systemd
timer is there for unattended sampling. A REPORTING command stores nothing when
replaying someone else's snapshot or when `--format json` is asked for, because
a command in a pipeline should not change state behind your back. `record` is
the exception, since storing a reading is its whole purpose: it writes under
both, which is how `lsdsk record --replay` folds an old capture into the
history.

`--no-record` opts out entirely and `--history-file` puts the store somewhere of
your choosing. Both are global, like `--profile`, so they go before the
subcommand: `lsdsk --history-file /var/lib/lsdsk.json record`. For a permanent
setting there is a `[history]` section with `enabled`, `path` and
`max_samples_per_drive`; `lsdsk config` shows the effective values and where
each came from. As root the store defaults to `/var/lib/lsdsk/history.json`,
because what it records is a property of the machine rather than of whoever
typed the command; a non-root run keeps a per-user path, since it could not
write there anyway. The first reporting run that records names the file,
once; `lsdsk record` writes silently unless asked for `--format json`.

Every value the tool judges or lays out by is a configuration key: `[thresholds]`
carries the wear bands, the CRC significance floor, the firmware-mismatch count
and the quiet-evidence figure, and `[display]` carries the assumed width when
output is piped, the summary cap, the wear floor a trend row must clear, whether
kernel-virtual devices are listed, the ceiling on the wwn column, and the
traceback limits. What is deliberately *not*
configurable is anything a specification fixes: register offsets, IOCTL codes,
the Kelvin offset, the 512-byte sector. Those are not choices, and a file that
could change them would break decoding rather than tune it. Turning recording off never stops history being *read*, so
findings stay graded against the past either way.

The `health` table carries the same distinction in the column you already read:
a count still climbing gets a trailing `+`, and one proved to have stopped drops
out of red. A red number that never changes is how a tool teaches you to ignore
red.

One consequence worth knowing if you alert on the exit code: a fault the record
proves is over is downgraded to a hint, and a hint is not counted as actionable.
A host whose only complaint was a long-dead cable fault moves from exit `1` to
exit `0`. That is the right answer, and it is a change if you gate a cron on it.

## Where could this card go

`lsdsk slots` puts the whole board on one screen: every PCIe port, what it is
capable of, what it negotiated, what occupies it, what that occupant actually
needs, and a verdict. A free port is named, and a card leaving bandwidth unused
is named with the figure, so a swap is obvious before you open the case.

```
Micro-Star International Co., Ltd. MEG Z690 ACE (MS-7D27)   18 ports   3 free
      port          slot  capable              running             occupant                                                                             needs                 verdict
   0000:00:01.0  #1    Gen5x8 (31.50 GB/s)  Gen3x8 (7.88 GB/s)  Advanced Micro Devices, Inc. [AMD/ATI] Hawaii XT / Grenada XT [Radeon R9 290X/390X]  Gen3x16 (15.76 GB/s)  in use (graphics)
   0000:00:01.1  #2    Gen5x8 (31.50 GB/s)  Gen2x8 (4.00 GB/s)  Intel Corporation 82599ES 10-Gigabit SFI/SFP+ Network Connection                     Gen2x8 (4.00 GB/s)    spare 27.50 GB/s
   0000:00:1c.0  #0    Gen3x1 (0.98 GB/s)   -                   empty                                                                                -                     FREE
   0000:00:1d.0  #12   Gen3x4 (3.94 GB/s)   Gen3x4 (3.94 GB/s)  Samsung Electronics Co Ltd NVMe SSD Controller PM9A1/PM9A3/980PRO                    Gen4x4 (7.88 GB/s)    port limits it
```

Four of that board's eighteen ports, at a terminal wide enough to keep the
bandwidths. An occupant is named by the PCI database rather than by the label on
the part, so it reads as the vendor and device the hardware reports itself as.

It does not say whether a port is an M.2 socket or a card slot, and that is
deliberate. The firmware slot table is the only thing carrying form factor, and
on three boards measured here it named no M.2 socket at all while getting most
of its bus addresses wrong. The view carries the board's own slot number
instead, which you match against the manual. Unused bandwidth is reported
whether or not a move can be proposed, because the figure is measured either
way.

## It tells you whose hardware you are looking at

Storage tools are routinely run inside a container or a guest, where the answer
means something different. lsdsk detects which and says so:

- In a **container** you are shown the host's real hardware through a shared
  kernel. The faults are genuine, they just belong to the host, and the missing
  SMART data is missing device nodes rather than privileges, so it will not send
  you off to try `sudo`.
- In a **virtual machine** the disks and link speeds are the hypervisor's
  invention, so the link and placement rules are suppressed rather than
  reporting cable faults on an emulated controller.
