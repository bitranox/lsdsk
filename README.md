# lsdsk

<!-- Badges -->
[![CI](https://github.com/bitranox/lsdsk/actions/workflows/default_cicd_public.yml/badge.svg)](https://github.com/bitranox/lsdsk/actions/workflows/default_cicd_public.yml)
[![CodeQL](https://github.com/bitranox/lsdsk/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/lsdsk/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/lsdsk?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/lsdsk.svg)](https://pypi.org/project/lsdsk/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lsdsk.svg)](https://pypi.org/project/lsdsk/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/lsdsk/graph/badge.svg?token=JKJR0XzLus)](https://codecov.io/gh/bitranox/lsdsk)
[![Maintainability](https://qlty.sh/gh/bitranox/projects/lsdsk/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/lsdsk)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)

See which disks hang off which controller, whether each one runs at the speed it
could, and how worn it is.

```bash
uvx lsdsk
```

That is the whole command. It prints the entire machine on one page: the
mainboard, what is wrong, the controller tree, every disk's identity, wear and
error counters, every SMART attribute, the PCIe slots, and each finding with its
reasoning. Nothing has to be selected and no subcommand has to be guessed, so
somebody who does not yet know what is wrong does not have to know what to ask
for. What is wrong comes first, so stopping after the first screen still shows
everything actionable, and each command in the table below is one section of the
same report for when you already know which one you want.

```
lsdsk  linux-sas-hba   19 disks on 5 controllers

    PROBLEMS      7 warning   6 hint
 !  /dev/nvme0n1  SAMSUNG MZVPV512HDGL-00000 logged 2 media errors
 !  /dev/sdc      Samsung SSD 870 EVO 4TB has 99345 interface CRC errors
 !  /dev/sdd      Samsung SSD 870 EVO 4TB has 2179485 interface CRC errors
 !  /dev/sde      Samsung SSD 870 EVO 500GB has 430 interface CRC errors
 !  /dev/sdj      Samsung SSD 870 EVO 4TB has 462640 interface CRC errors
 !  /dev/sdl      Hitachi HDS722020ALA330 has 4 reallocated sectors
                  and 7 more, run `lsdsk findings`

Topology on linux-sas-hba
   0000:00:1f.2  Intel Corporation C600/X79 series chipset 6-Port SATA AHCI Controller  ahci

~  0000:03:00.0  HBA 9500-16i  mpt3sas  fw 23.00.00.00  PCIe 3.0 x8 of 4.0 x8   21 ports, 11 free
      device        model                       size  kind  bus   port     disk     link     temp  worn
|- ~  /dev/sda      Samsung SSD 870 EVO 4TB     3.6T  SSD   SATA  12G      6G       6G        36C    1%
|- ~  /dev/sdb      Samsung SSD 870 EVO 500GB   466G  SSD   SATA  12G      6G       6G        30C    2%
'- !! /dev/sdd      Samsung SSD 870 EVO 4TB     3.6T  SSD   SATA  12G      6G       6G        37C    1%

Controllers on linux-sas-hba        ...
Disks on linux-sas-hba              ...
Disk health on linux-sas-hba        ...
SMART attributes on linux-sas-hba   ...
PCIe slots on linux-sas-hba         ...
Counter trends on linux-sas-hba     ...
Findings on linux-sas-hba           ...
```

Three speeds per drive, because they answer different questions. `port` is what
the seat can give, `disk` is what the drive can do, and `link` is what the two
of them actually agreed on. An orange `disk` means the drive cannot use the port
it occupies, which is a placement question. A red `link` means both ends could
have gone faster and did not, which is a fault.

## Why this exists

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
port  disk  link
6G    6G    6G    everything agrees, nothing to say
12G   3G    3G    a 3 Gb/s drive occupying a 12 Gb/s seat
3G    6G    3G    the port is the limit, the drive could do more
6G    6G    3G    both ends can do 6 and the link cannot: a fault
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
the alerts, which is the one outcome nobody designed for.

lsdsk grades severity down whenever the machine could not have done better. A
PCIe 4.0 card in a Gen3 board gets a dim hint instead of a warning, with the
arithmetic attached: the ten drives on it want about 6.00 GB/s and the link
carries 7.88 GB/s, so the ceiling is real and costs you nothing today. Revisit
it when the drive count grows.

That restraint is the feature, not good manners. A tool that only ever escalates
teaches you to ignore it. One that tells you when not to worry is worth
believing on the day it says replace this drive.

In the same spirit, it publishes a list of what it cannot know. Read that one
before you quote any of its numbers at your manager.

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
  to move it to, or a card that would lose nothing by swapping places with it.
- A controller capped by the mainboard, with the PCIe generation that would lift
  it and whether the attached drives can even use the difference.
- A drive held back by a slower port than it supports.
- Wear-out, reallocated, pending and uncorrectable sectors, and NVMe media
  errors, each against the drive's own declared thresholds.
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

## Commands

| Command                    | Shows                                                            |
|----------------------------|------------------------------------------------------------------|
| `lsdsk`                    | Everything, on one page. Each command below is one section of it |
| `lsdsk topology`           | The problem summary and the disk-to-controller tree              |
| `lsdsk controllers`        | Controllers, PCIe placement, free ports, load                    |
| `lsdsk disks`              | One row per disk                                                 |
| `lsdsk health`             | Wear, temperature, hours and error counters                      |
| `lsdsk smart`              | Every disk's SMART attributes against its thresholds             |
| `lsdsk findings`           | Every finding with its reasoning and its remedy                  |
| `lsdsk slots`              | Every PCIe port: capability, occupant, what is free              |
| `lsdsk trend`              | What each error counter is doing over time, not just its total   |
| `lsdsk record`             | Store one reading and print nothing, for a timer                 |
| `lsdsk tui`                | An interactive page per question, `*top` style                   |
| `lsdsk snapshot -o f.json` | Capture the raw reading                                          |
| `lsdsk --replay f.json`    | Render a capture from any machine                                |

The interactive view is keyed the way the `*top` family is: `1` to `8` or the
matching function key switch page, and `q` quits; the footer lists those, so
there is nothing to memorise. `left`, `right` and `tab` also step between pages,
and `r` rescans, without appearing in the footer.
The pages carry the same names as the commands, in the same order, so `4` and
`lsdsk health` are the same view.
Each page stands alone and shows every disk, so nothing has to be selected to
see it, and `up` and `down` scroll whichever page is on screen.

`--format json` gives a machine-readable envelope naming the command that
produced it, on every command that produces data. Exit codes are `0` for nothing
actionable and `1` when a warning or critical was found, so it drops straight
into a monitoring check. Errors use sysexits conventions rather than a single
code: `13` when something needs privilege this run lacks, `22` for a
configuration section or a `--profile` name the configuration library
rejects, `78` for a file that is not a
snapshot this version reads or a platform with no hardware reader. Treat
anything above `1` as "did not run".

`2` is Click's usage error and means the command line was wrong, not that a file
was missing: an unknown option, an unknown command, a missing argument and a bad
`--format` choice all produce it, alongside a `--replay` path that is not there.

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
device        counter           total  change  over  per hour  verdict
/dev/sdc      interface CRC     99361     +16   16h       1.0  rising
/dev/sdd      interface CRC   2196127  +16642   15h      1109  rising
/dev/sde      interface CRC       430      +0   15h         -  too soon to say, only 0.6 were due
/dev/sdj      interface CRC    462640      +0   16h         -  no new in 16h, 235 were due
```

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
write there anyway. The first run that records names the file, once.

Every value the tool judges or lays out by is a configuration key: `[thresholds]`
carries the wear bands, the CRC significance floor, the firmware-mismatch count
and the quiet-evidence figure, and `[display]` carries the assumed width when
output is piped, the summary cap, the temperature bands used for drives that
publish none of their own, and the traceback limits. What is deliberately *not*
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
MSI MEG Z690 ACE (MS-7D27)   18 ports   3 free
   port          slot  capable  running  occupant                        needs     verdict
   0000:00:01.0  #1    Gen5 x8  Gen3 x8  AMD Hawaii XT [Radeon R9 290X]  Gen3 x16  in use (graphics)
   0000:00:01.1  #2    Gen5 x8  Gen2 x8  Intel 82599ES 10G SFI/SFP+      Gen2 x8   spare 27.50 GB/s
   0000:00:1d.0  #12   Gen3 x4  Gen3 x4  Samsung 980 PRO 2TB             Gen4 x4   port limits it
   0000:00:1c.0  #0    Gen3 x1  -        empty                           -         FREE
```

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

## Privileges

It runs unprivileged and says what that costs. Topology, PCIe link state, SATA
capability and negotiated speed, SAS phy rates, capacity, controller firmware
and NVMe temperature all read without any privilege at all.

Four things need root or Administrator, and none is guessed without it:

- **SMART attributes and wear.** Those columns read `-`.
- **The error counters, so `trend` and `record`.** Same passthrough read, so an
  unelevated run records nothing at all and no trend can be built from it.
- **PCIe slot numbers and whether a port is a real connector.** The capability
  structures holding them sit past the first 64 bytes of config space, which is
  where an unprivileged read stops. Without them the `slot` column reads `-`,
  and no card move is ever proposed, because a port that cannot be confirmed as
  a physical connector might be soldered-down silicon.
- **The AHCI capability register**, which needs the controller's BAR5 mapped and
  carries both the ports-implemented bitmap, giving a SATA controller's
  free-port count, and the speed the port itself can carry, which is the `port`
  column on every SATA row. Without it both read `-`. This one is refused on
  some hosts even as root, so a `-` there is not proof of an unprivileged run.

In a container none of it comes back, because the device nodes are not there to
read; elevating changes nothing.

Spare bandwidth on a port is reported either way: it is a measurement, and a
measurement is shown whether or not a card could actually be moved there.

## It ships a skill for Claude Code

The hard part of a storage report is not reading it, it is knowing which findings
deserve action. lsdsk ships that judgement as a Claude Code skill, so an agent
reading the output reaches the same conclusions a practised admin would.

```
/plugin marketplace add bitranox/lsdsk
/plugin install lsdsk
```

The skill teaches what the tool cannot: that a CRC count is the cable and never
the drive, that a wear percentage means nothing without the drive's own
threshold, that a controller capped by the board has two opposite remedies
depending on whether a faster port exists and is merely occupied, and that a
slot number is matched against the mainboard manual because no readable source
gives the form factor. It also says where to go when lsdsk stops: the tool makes
no network request by design, but an agent can fetch the board manual or the HBA
datasheet, and the skill says which questions that answers and how to keep the
looked-up figures apart from the measured ones.

## Install

```bash
uvx lsdsk              # run without installing
uv tool install lsdsk  # install for repeated use
pip install lsdsk
```

Python 3.11 or newer, Linux or Windows. It shells out to nothing: no
`smartmontools`, no `nvme-cli`, no `lspci`, no subprocess of any kind, and no
network access at any point. Its own Python dependencies are declared in
`pyproject.toml`.

## How it works

Linux reads sysfs and issues `SG_IO` ATA passthrough and NVMe admin ioctls
directly. A SATA port's own speed comes from the AHCI controller's capability
register, because `libata` publishes a port speed only once a limit has been
applied to it, so on healthy hardware sysfs has no answer at all. Windows uses `SetupAPI` and `DeviceIoControl` through `ctypes`, with
no WMI and no PowerShell. Both platforms receive the same ATA IDENTIFY, ATA
SMART and NVMe structures, so a single set of decoders serves both and is tested
against captures from real hardware on every supported operating system.

Every command it issues is a read. It never writes to a device.

## Documentation

| Document                                                                       | What it covers                                                                       |
|--------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| [INSTALL.md](INSTALL.md)                                                       | Installing it, and what works unprivileged versus what needs root                    |
| [CONFIG.md](CONFIG.md)                                                         | Every configuration key, the layered sources, and the env-var forms                  |
| [DEVELOPMENT.md](DEVELOPMENT.md)                                               | Working on lsdsk: the gate, the test lanes, capturing a fixture                      |
| [CONTRIBUTING.md](CONTRIBUTING.md)                                             | How to propose a change                                                              |
| [SECURITY.md](SECURITY.md)                                                     | Reporting a vulnerability                                                            |
| [CHANGELOG.md](CHANGELOG.md)                                                   | What changed, and when                                                               |
| [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) | Every module, the layer rule, the CLI commands and the exit codes                    |
| [ai-transparency.md](ai-transparency.md)                                       | Where an AI assistant was used, what was verified on real hardware, and what was not |
| [ai-stance.md](ai-stance.md)                                                   | Why the project takes that position                                                  |

## Licence

MIT.
