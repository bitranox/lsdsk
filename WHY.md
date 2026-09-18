# Why this exists

**English** | [Deutsch](de/WHY.md)

The problem lsdsk was written for, and the two cases that are easiest to get
wrong without it. Back to the [README](README.md).

Anyone who looks after a server with a lot of disks knows the moment. Something
has failed, and the picture goes cloudy exactly when it needs to be sharp:

- Which drive is it?
- Where does it hang, physically and logically?
- How are the others on the same controller doing?
- Is everything running at the link speed it should be?
- Where could another drive go, and is there bandwidth to feed it?
- any potential cabling or tray problems like CRC Errors otherwise unnoticed?
- SMART Status? 

Every answer is somewhere in `lspci`, `lsblk`, `smartctl` and `nvme`, in four
formats that agree on nothing, and you assemble it by hand at the worst possible
time. On Windows you do not assemble it at all.

The obvious response is to gather all of that into one place and print it
neatly. Useful, and most tools stop there.

Gathering is the easy half. A number on its own is inert. `3.0 Gb/s` means
nothing until you also remember what that particular drive is capable of, and
nobody remembers that for eighteen drives at two in the morning. Set the two
side by side and the remembering stops being your job:

```
port             disk             link
6G (0.60 GB/s)   6G (0.60 GB/s)   6G (0.60 GB/s)   everything agrees, nothing to say
12G (1.20 GB/s)  3G (0.30 GB/s)   3G (0.30 GB/s)   a 3 Gb/s drive occupying a 12 Gb/s seat
3G (0.30 GB/s)   6G (0.60 GB/s)   3G (0.30 GB/s)   the port is the limit, the drive could do more
6G (0.60 GB/s)   6G (0.60 GB/s)   3G (0.30 GB/s)   both ends can do 6 and the link cannot: a fault
```

Same bytes, off the same drive, from the same commands. What changed is only
what they sit next to. That is most of what lsdsk is: every reading placed
against what it should have been, so you get a verdict instead of arithmetic.

## The failure nobody notices

Now the more interesting case, the one where nothing is broken at all.

An old 3 Gb/s drive plugged into your only 6 Gb/s port is not a fault. It runs
at its rated speed, reports perfect health, and no monitoring system on earth
will ever mention it. Meanwhile a 6 Gb/s drive somewhere else in the same
chassis is running at half speed because the fast port was already taken.

Both drives are individually fine. The arrangement is not, and the fix is
swapping two cables. lsdsk looks for exactly that pairing and names both drives,
because this is the kind of thing that survives for the life of a machine: too
small to alarm anybody, too cheap to leave sitting there.

Dead drives get attention because they are loud. This is the other kind.

## It will argue you out of a purchase

Most monitoring inflates. Everything becomes a red alert, so you stop reading
the alerts.

lsdsk grades severity down whenever the machine could not have done better. A
PCIe 4.0 card in a Gen3 board gets a dim hint instead of a warning, with the
arithmetic attached: the ten drives on it want about 6.00 GB/s and the link
carries 7.88 GB/s, so the ceiling is real and costs you nothing today. Revisit
it when the drive count grows.

That restraint is the feature, not good manners. A tool that only ever escalates
teaches you to ignore it. One that tells you when not to worry is worth
believing on the day it says replace this drive.

In the same spirit, it publishes a list of what it cannot know. Read it
before you quote any of its numbers at your manager.
