---
name: lsdsk
description: Use when inspecting or diagnosing storage hardware - which disk hangs off which controller, whether a SATA, SAS or NVMe link runs at the speed both ends support, whether a controller sits in a slot worthy of it, how worn an SSD is, whether reallocated or pending sectors are climbing, drive temperatures, controller firmware, or where there is room for another drive. Also use when reaching for lspci, lsblk, smartctl or nvme-cli to answer any of those, and when reading an lsdsk report and deciding what to actually do about a finding.
---

# lsdsk

Groups disks by the controller they hang off, compares every link against what
both ends of it could do, and reports what is worth acting on. It also names the
mainboard, from DMI, which is what makes its placement advice actionable. Linux
and Windows, no subprocesses, no network. It runs anywhere `--replay` is all you
need, macOS included; only reading real hardware is the two.

**Bare `lsdsk` gives a PERSON the interactive view and a PROGRAM the printed
page.** It looks at whether both ends are a terminal: where they are it opens
the full-screen application, because the whole machine on one page is more than
a reader takes in at once; where they are not - a pipe, a redirect, a CI log -
it prints the page. The exit code is the findings' in both, so `lsdsk; echo $?`
means the same thing either way. `lsdsk report` asks for the page by name,
whatever the terminal looks like.

**Being a subprocess does NOT mean you get the page. Say `lsdsk report` in
anything unattended.** Some callers hand their child a pseudo-terminal on both ends, and
there lsdsk cannot tell a program from a person: it opens the application and
waits for a keypress nobody can send. Measured in a notebook, where a CI job
hung on a bare `!lsdsk` for 900 seconds and was killed - IPython runs `!cmd`
under pexpect, so every notebook frontend does this. `script`, expect and
pty-allocating job runners allocate the same way, so treat them the same.

So `!lsdsk report` in a notebook cell, and `lsdsk report` in any scheduled or
unattended job, rather than reasoning about whether that caller counts as a
terminal. Getting it wrong costs a hang, and getting it needlessly right costs
nothing.

`lsdsk report` takes `--replay` like any other command, and the global options
still come first: `lsdsk --no-record report --replay file.json`.

That distinction matters when you TELL somebody to run it. If they want the
page on screen rather than the application, say `lsdsk report`.

For a TICKET or a handover, still send a snapshot rather than redirected text:
`lsdsk snapshot -o file.json` captures the raw reading, so the recipient can
replay every section at any width and any privilege question is settled by the
capture itself. A `lsdsk > report.txt` is a picture of one moment at one width.

The page is the whole report: mainboard, problem summary, controller tree,
the controller table, disk identities, wear and error counters, SMART
attributes, PCIe slots, counter trends, and every finding with its reasoning, in
that order. It already contains `trend` and `slots`, so running those again
after a bare `lsdsk` adds nothing. Run it first and nothing else, for a handover, a
ticket, or any machine you do not already know. Every subcommand is one section
of it, for when you already know which section you want. Unprivileged it still
renders every section and never aborts; what it could not read shows `-` and the
header names it, so check that line before quoting a counter as zero.

Bare `lsdsk` takes no `--format`: it is a group rather than a command. For a
structured capture of everything, use `lsdsk snapshot -o file.json`; for one
section's envelope, `lsdsk <section> --format json`.

## Running it

```bash
uvx lsdsk                     # at a terminal the interactive view, elsewhere the page
uvx lsdsk report              # everything on one page, by name. Start here
uvx lsdsk topology            # the problem summary and the disk-to-controller tree
uvx lsdsk findings            # every finding, with reasoning and a remedy
uvx lsdsk health              # wear, temperature, hours, error counters
uvx lsdsk smart               # every disk's SMART attributes against its thresholds
uvx lsdsk controllers         # controllers, PCIe placement, free ports
uvx lsdsk slots               # every PCIe port: what holds it, what is free
uvx lsdsk disks               # one row per disk
uvx lsdsk trend               # what each error counter is DOING, not just its total
uvx lsdsk record              # store one reading, print nothing, for a timer
uvx lsdsk tui                 # the same eight views, interactive (1-8, left/right, q)
uvx lsdsk snapshot -o m.json  # capture the raw reading
uvx lsdsk info                # version, homepage and the shell command name
uvx lsdsk config              # show effective configuration
uvx lsdsk config-deploy --target user   # write ~/.config/lsdsk so it can be edited (app|host need root)
uvx lsdsk config-generate-examples --destination DIR   # scaffold examples
uvx lsdsk --replay m.json     # render a capture from any machine
```

`--replay` works in either position on every command that reads a machine and
means the same thing, so `lsdsk --replay m.json health` and
`lsdsk health --replay m.json` are the same run; give both and the command's own
wins. `snapshot` is the exception: it captures the machine it runs on and
refuses `--replay` outright, as below. `--profile` behaves the same way on the
`config` commands. `--history-file` and `--no-record` exist only before the
command, so `lsdsk --history-file h.json record` is right and
`lsdsk record --history-file h.json` is the usage error that exits `2`. When in
doubt, `lsdsk <command> --help` lists what that command takes.

`fail` and `logdemo` also exist. They are not diagnostic commands: they are the
vehicles the traceback and logging tests drive through the real entry point.
Never reach for them to answer a question about hardware.

## Devices with no hardware behind them

A zram swap device, a loop mount, a ZFS zvol, a device-mapper or mdraid node:
the kernel provides these itself, so they have no controller, no link and no
SMART. lsdsk reads them, says so, and does not judge them - no rule fires on one
and no finding names one, because there is nothing to compare against.

It decides by asking the kernel where the device sits, never by its name. A
device with no hardware parent resolves under `/sys/devices/virtual`; a real one
resolves under its PCI path. So an optical drive, named `sr0`, is ordinary
hardware occupying a real port and is counted as one.

They are folded away rather than listed, because a Proxmox host has more of them
than drives. The topology tree and the disk table each end with a line saying
how many and of what:

```text
kernel-virtual devices, with no controller and no counters
   12 not listed: 8 loop, 3 zd, 1 zram   (--expand-virtual lists them)
```

`--expand-virtual` gives each one a row, before or after `topology`, `disks` and
`tui`. To make that the default on a host whose zvols are the point, set the
configuration key - `lsdsk --set display.expand_virtual=true disks` for one run,
or `expand_virtual = true` under `[display]` in the deployed
`config.d/70-display.toml` to keep it:

```bash
uvx lsdsk config-deploy --target user     # then edit ~/.config/lsdsk/config.d/70-display.toml
```

**This setting changes the screen and nothing else.** The devices are always
read, always counted in the header, and always present in `--format json`. A
program never has to ask for them.

`--format json` gives a machine-readable envelope on every command that
produces data, including `info`, `snapshot`, `record` and all three
`config` commands. `tui`, `fail` and `logdemo` have none, having no data to structure.
It carries `ok`, `command`, `data` and `skipped`,
so a caller can tell a complete answer from a partial one.

**The envelope's four keys, and what a caller may rely on.** `ok` is true when
the command did everything asked of it, and false when something was left
undone - it is NOT "the hardware is healthy", so never alert on it. `skipped` is
a list of sentences saying what was not done and why, empty when nothing was.
`command` names the command that produced the payload. `data` is that command's
own result.

**A finding, inside `data.findings`, has five string fields: `severity`,
`subject`, `title`, `detail`, `action`.** `severity` is exactly one of
`critical`, `warning`, `hint` - those three words, lower case, and no others.
That is what a monitor branches on, and it is the only way to separate critical
from warning, because the exit code cannot: `1` means "warning OR critical", so
a check that must fire only on critical has to read the field.

```bash
lsdsk findings --format json |
  python3 -c 'import json,sys; d=json.load(sys.stdin);
  sys.exit(any(f["severity"] == "critical" for f in d["data"]["findings"]))'
```

**A disk, inside `data.disks`, carries `node`, `path`, `model`, `serial`,
`firmware`, `wwn`, `size_bytes`, `kind`, `bus`, `controller_address`, `link`,
`pcie` and `health`.** Every one but `node`, `path`, `model` and `bus` may be
`null`, which means it was not read rather than that it is zero. `bus` is one of
`sata`, `sas`, `nvme`, `usb`, `virtual`, `unknown`; `kind` is `ssd`, `hdd` or
`unknown`. `link` is an object of `negotiated_gbps`, `drive_max_gbps` and
`port_max_gbps`, and a speed rule only fires when both ends are known.

**`data.virtual_disks` is a second list of the same shape**, holding the devices
with no hardware behind them. It is always populated, whatever `--expand-virtual`
or `display.expand_virtual` says, and `data.disks` never contains one - which is
what a check for "nothing without a transport among the real drives" reads:

```bash
lsdsk disks --format json |
  python3 -c 'import json,sys; d=json.load(sys.stdin)["data"];
  sys.exit(any(x["bus"] == "virtual" for x in d["disks"]))'
```

That check is Linux-only, deliberately. On Windows `bus` is `virtual` for a
HYPERVISOR disk, which is the machine's real storage, so the same line would
fail on a healthy guest. It is `data.virtual_disks`, not the bus value, that
means "provided by the kernel with nothing behind it" - so the check that holds
on either platform asserts the two lists stay disjoint:

```bash
lsdsk disks --format json |
  python3 -c 'import json,sys; d=json.load(sys.stdin)["data"];
  v={x["node"] for x in d["virtual_disks"]};
  sys.exit(any(x["node"] in v for x in d["disks"]))'
```

**`snapshot` is the exception to all of this.** With `-o` it writes the raw
reading - the bytes the platform gave, for `--replay` - which is a different
document from the envelope above and is not a list of disks.

**A monitor must read `data.privileged` too, or it reports clean on a blind
run.** Unprivileged, no SMART is read, so no wear or counter finding is ever
raised and the check above passes on a machine nobody looked inside.
`data.privileged` and `data.devices_accessible` are both booleans in the same
payload: treat `privileged` false as "unknown", never as "healthy".

**In a Python program, skip the subprocess.** `lsdsk.adapters.hw.snapshot`
gives an inventory and `lsdsk.domain.diagnostics.diagnose` gives the findings as
objects, with the same rules the CLI runs:

```python
from pathlib import Path

from lsdsk.adapters.hw import snapshot
from lsdsk.domain.diagnostics import diagnose

inventory = snapshot.load(Path("capture.json"))  # or snapshot.collect() for this machine
findings = diagnose(inventory)  # each has .severity, .subject, .title, .detail, .action
```

`diagnose` returns a tuple and takes two more keyword arguments the CLI fills
in: `thresholds`, and `history` for the trend rules. Omit them and you get the
SHIPPED defaults with no history, which is not what the same machine's `lsdsk`
would report if its configuration deploys different thresholds.

The package's own `__all__` holds `get_config` and `print_info`, which are the
configuration loader and the `info` command's printer; neither is what you want
for hardware. Reading a machine needs privileges exactly as the CLI does, and
`snapshot.load` needs none. Run `lsdsk --help` and
`lsdsk <command> --help` for current options rather than trusting a list here.

The global options are `--replay`, `--profile`, `--history-file`,
`--no-record`, `--expand-virtual`, `--traceback`, `--env-file`, `--version` and
`--set SECTION.KEY=VALUE`, the last being how you move a judgement for one run.
Three also work after the subcommand: `--replay` on any command that reads a
machine, `--profile` on the `config` commands, and `--expand-virtual` on
`topology`, `disks` and `tui`. Every other global option is refused after the
subcommand with exit `2`.

The figures the rules turn on are all `[thresholds]` keys, so none of them is
fixed: `wear_warning_percent` 80, `wear_critical_percent` 95,
`crc_errors_significant` 100 (below it a CRC count is a hint), `quiet_expected_min`
10.0 (the line the whole "were due" idea rests on: fewer expected than this and
the tool refuses to call a counter quiet), `min_span_hours` 1 and
`mixed_firmware_threshold` 2. Override one for a run with
`lsdsk --set thresholds.crc_errors_significant=10 findings`, or permanently by
editing the deployed `config.d/60-thresholds.toml`.

Exit codes: `0` nothing actionable, `1` a warning or critical. Hints never set a
non-zero code; a hint is a ceiling, not a fault.

**Only the eight reporting commands and bare `lsdsk` set `0`/`1` from findings.**
`record`, `snapshot` and the `config-*` commands exit `0` on success whatever
the hardware says, so never alert on their code. And `1` is also what an
internal error leaves, so read stderr before treating it as a finding;
`lsdsk findings --format json` is the unambiguous test.

| Code | Means                                                                                                            |
|------|------------------------------------------------------------------------------------------------------------------|
| `0`  | A reporting command found nothing actionable. `record`, `snapshot` and `config-*` exit `0` on success regardless |
| `1`  | A reporting command found a warning or a critical. An internal error also leaves `1`; see below                  |
| `2`  | The command line was wrong. See below, this one is misread constantly                                            |
| `13` | `config-deploy --target app` or `host` without root. A diagnostic run never exits 13: it degrades to `-`         |
| `22` | A configuration section does not exist, a `--profile` was rejected, or `snapshot` was given `--replay`           |
| `78` | The file is not a snapshot this version reads, or this platform has no hardware reader                           |

**`2` does not mean the file was missing.** It is the CLI framework's usage
error and an absent `--replay` path is only one of its causes: an unknown
option, an unknown command, a missing required argument, an invalid `--format`
value, a `--history-file` that exists but cannot be opened (a store whose CONTENT is
wrong is a different thing: it warns and the run continues), a malformed `--set`
and a
`--history-file` or `--no-record` placed after the subcommand all produce it
too. A wrapper that reads `2` as "the capture is
absent" will page whoever owns the capture pipeline when the actual fault is a
typo in the wrapper's own command line, and will keep doing so until somebody
reads the message on stderr. Read that message before concluding anything; it
names which it was.

**The exit code can fall from `1` to `0` with the hardware untouched**, and if
somebody alerts on it they need to know. Two ways. A fault the recorded history
proves is over is downgraded to a hint, and a hint is not actionable, so a host
whose only complaint was a dead cable fault goes quiet - correctly. And an
unprivileged run reads no SMART at all, so the findings are never raised in the
first place; that one is a blind run, not good news. Tell them to pin the
privilege level and read the header for `-` columns rather than trusting `0`.

**An ELEVATED run writes to disk.** Reading the counters needs root, so an
unelevated run records nothing at all and its store never appears. A run that
can read them records every drive it read, owner readable only and capped per
drive (`history.max_samples_per_drive`), and only when some drive's own clock -
its power-on hours - has moved on since the last one. That covers the drives
whose own clock stood still too, and such a drive has its newest row REPLACED
rather than gaining one: two readings inside one power-on hour hold one hour of
information. So the store does not grow a row per drive per run, and a drive's
newest row is always its latest reading. That is what makes `lsdsk trend`
possible.

**Where it lives depends on who is running.** A root run on Linux or macOS uses
`/var/lib/lsdsk/history.json`; anyone else gets the per-user state directory:
`$XDG_STATE_HOME/lsdsk/history.json` or `~/.local/state/lsdsk/history.json` on
Linux, `~/Library/Application Support/bitranox/lsdsk/` on macOS,
`%LOCALAPPDATA%\bitranox\lsdsk\` on Windows. Reading
the counters needs root, so on a server it is the ROOT path that fills while the
per-user one stays empty. When a store is refused it is therefore
`/var/lib/lsdsk/history.json` you move aside on a server. The two paths are
separate files, so an unprivileged run neither reads nor is refused by the root
one; it has its own, which stays empty because recording needs root. `lsdsk record --format json` prints the path this run resolved, under
`store`.

A REPORTING
command does not write when replaying somebody else's snapshot or when
`--format json` is asked for. Global `--no-record` turns it off and
`--history-file` moves it; both go before the subcommand. `--no-record` still
*reads* the history and still grades against it, so it suppresses the write
without blinding the verdict.

**`lsdsk record` is the exception, and a read-only pipeline must exclude it.**
Storing a reading is its entire purpose, so it writes even under `--replay` and
even under `--format json` - `lsdsk record --replay other.json` is how you fold
somebody else's capture into the history deliberately. It prints nothing in its
human form, which is what suits it to a timer, but `--format json` gives it the
same envelope every other command has, with `recorded`, `store` and `drives`
inside `data`.
So the rule is "a REPORTING command asked for JSON does not mutate state", not
"lsdsk does not mutate state": put `topology`, `disks`, `health`, `smart`,
`findings`, `slots`, `controllers` or `trend` in that pipeline, and leave
`record`, `snapshot` and the two `config-*` commands out of it, all four of
which write by design whatever `--format` says. `snapshot` also REFUSES a global
`--replay` with exit `22` rather than obeying it: it always captures the machine
it runs on, so there is no snapshot of somebody else's capture to take. Copy the
file instead. `info` and plain `config` write
nothing either and are safe to include; of the three `config` commands, `config-deploy`
and `config-generate-examples` are the two that create files.

**A run that cannot READ the history will not WRITE it either, and says so.**
On stderr you get `Warning: ignoring counter history: <why>` followed by
`Not recording this run, so <path> is left as it is.` The causes are a store
belonging to a different hostname, one written by a newer lsdsk, one too large
to read, and one that is not valid JSON. Two things follow, and both matter to
whoever is paged. The hardware is still diagnosed and the exit code still
reflects the findings, so this is not a failed run. And the file is INTACT:
nothing was overwritten, so there is nothing to restore from backup, and the
counters simply stop accumulating until somebody acts. To resume recording,
move the file aside or point `--history-file` somewhere else - a renamed host
is the common cause, because the default path carries no hostname.

Topology, link speeds, capacity and firmware read unprivileged. It never guesses
a value it could not read, so anything below shows `-` instead, and the header
says so.

Four things need root or Administrator, not one:

| Needs privilege                         | Because                                   | Costs you                                        |
|-----------------------------------------|-------------------------------------------|--------------------------------------------------|
| SMART attributes and wear               | ATA and NVMe passthrough ioctls           | Those columns, and every finding drawn from them |
| Error counters, so `trend` and `record` | Same passthrough read                     | An unelevated run records nothing at all         |
| PCIe slot numbers, connector detection  | Config space past the first 64 bytes      | The `slot` column, `FREE`, and any card move     |
| The AHCI ports-implemented bitmap       | A memory mapping of the controller's BAR5 | A SATA controller's free-port count              |

The last one is refused on some hosts even as root, so a `-` there is not proof
the run was unprivileged.

**Root does not always help.** In a container the device nodes do not exist, so
elevating changes nothing. Check what kind of machine you are on before
arranging access you cannot use.

**On Windows a port's own speed and width are not a privilege question.** Windows
publishes link registers for PCIe endpoints and none at all for bridges, so a
port's `capable` and `running` columns stay `-` and `upstream` stays null however
the run was started. Measured on one board: eight bridges, not one with a link
speed, and no registry key, WMI class or user-mode API that has them. Never tell
a Windows user to re-run elevated to reveal a port's capability - they will come
back with the same `-` and think something is broken.

**An unprivileged run that reports nothing is not a clean bill of health.** It
never read SMART, so those findings were never raised. That is a blind run, not
a quiet one.

## Check what kind of machine you are on first

The banner says so, and it changes the whole reading:

- **Bare metal.** Everything applies.
- **A container.** The disks and controllers shown belong to the host, seen
  through a shared kernel. The faults are real and worth reporting, but they are
  the host's faults, so investigate and act there. Health data is usually absent
  because the device nodes do not exist in the container, and elevating does not
  change that.
- **A virtual machine.** The disks, controllers and link speeds are the
  hypervisor's invention, so the link and placement rules are suppressed
  entirely: a cable warning about an emulated controller is noise. Health data
  can still be real if a device was passed through. Diagnose the host.

A container or a guest adds a caveat line under the banner; **bare metal adds
nothing**, so silence is the bare-metal answer rather than a missing check.
`--format json` states it outright in `data.environment`, one of `bare_metal`,
`virtual_machine`, `container` or `unknown`, with `data.environment_detail`
naming the hypervisor. Treat `unknown` as "not established", not as bare metal.

Never carry a link or placement recommendation from a guest to the host. Run it
on the host.

## A count is not a rate. Run `lsdsk trend` before advising anything

**Every error counter is a lifetime total held in the drive's own non-volatile
table.** It survives reboots, power cycles and reinstalls, and the host cannot
clear it. So a large number tells you how much damage there has ever been and
nothing at all about when. A fault that ended two years ago and one corrupting
data right now produce the same figure.

`lsdsk trend` is the answer, and it answers today:

```bash
uvx lsdsk trend
```

```
device        counter           total  change  span  per hour  verdict
/dev/sdd      interface CRC   2196127  +16642   15h      1109  rising
/dev/sdj      interface CRC    462640      +0   16h         -  no new in 16h, 235 were due
/dev/sde      interface CRC       430      +0   15h         -  too soon to say, only 0.6 were due
```

Those two top rows are the same drive model on one host with comparable totals.
`sdd` is failing now. `sdj` is a cable somebody already reseated. **Never send
somebody to reseat a cable for a `no new` row** - the fault is over, and the
work fixes nothing.

Rates are per power-on hour of the drive, not per hour of wall clock, so they
hold on a machine that is mostly switched off.

**`trend --format json` does not carry the trend.** Every reporting command
returns the same machine-wide envelope, and none of its fields is the verdict,
the rate or the `were due` figure - those exist in the human table only. A
rising counter reaches JSON only where it also produced a finding, inside that
finding's `detail`. Drive an automated decision from `findings`, and read the
human table when you need the rate.

`trend` says what is moving; `lsdsk findings` says how bad it is and what to do,
already graded by the same history. Pair them: pick the drive from `trend`,
quote the remedy from `findings`. When several drives are rising and only one
can be fixed now, rank by the rate, not by the total.

**Read the refusals as refusals.** `too soon to say` is not `no new`. Silence
counts only where the drive's own lifetime rate says errors were due in that
span, which is what the `were due` figure is: 235 expected and none seen is
evidence, 0.6 expected and none seen is nothing. Do not upgrade a `too soon to
say` into an all-clear.

There is deliberately no fixed waiting period to quote, because the right one
differs per drive: a drive erroring a thousand times an hour proves itself quiet
within hours, and one at 0.04 an hour would need months. Quote the `were due`
figure instead of inventing a window.

`counter reset` means the current total is BELOW what was recorded, which a
drive's own counter cannot do. The drive was swapped in that bay, its identity
changed, or the store belongs to other hardware. The rate is discarded; treat
the count as a first sample and check which drive you are looking at before
acting on it.

`first sample` means one reading exists. Say so and give the date a comparison
becomes possible; do not turn a single reading into a replacement decision.

**To get an answer the same day**, sample a few hours apart rather than waiting
for a daily job: a reading is only stored once the drive's own clock has moved
on, so runs minutes apart add nothing. Counters need root, so a run that is not
elevated records nothing at all.

A snapshot is still how you inspect a server from your desk or attach a
reproducible state to a bug report, and `lsdsk record --replay` folds one into
the history. Snapshots and the history store both contain drive serial numbers.

```bash
uvx lsdsk snapshot -o /var/lib/lsdsk/$(hostname)-$(date +%F).json
```

## Reading a finding

Each disk row carries three speeds: `port` is what the seat can give, `disk` is
what the drive can do, `link` is what they agreed on. In the structured output
these are GT/s, and the generation is 2.5=Gen1, 5=Gen2, 8=Gen3, 16=Gen4, 32=Gen5,
64=Gen6. Compare them to find the
constraint. An orange `disk` means the drive cannot use its port, a placement
question rather than a fault; a red `link` means both ends could have gone
faster, which is a real one. A **yellow `link`** is a shortfall with only ONE end
measured: real, but not yet attributable, so establish what the port can carry
before touching a cable - this is what an unprivileged run and a legacy bridge
both produce. A **yellow `port`** means the seat is the slower of the two, so
the drive is fine and the port is the constraint. Markers repeat every finding
for readers without colour: `~` hint, `!` warning, `!!` critical.

**Five rows move with the recorded history and the rest are fixed.** Interface
CRC errors and the three sector counters plus media errors are the ones the
history regrades, by at most one step in either direction: a count proved to be
climbing is escalated and one proved to be over is stood down. Everything else
is decided by rule, including wear, which crosses from warning to critical at
its own threshold and not because of anything recorded. Either way read the
marker on the finding in front of you, which is what set the exit code.

| Finding                                       | Means                                                           | Do                                                                                         |
|-----------------------------------------------|-----------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| Link below what both ends support             | Cable, backplane slot or connector                              | Reseat, swap cable, try another bay, before suspecting the drive                           |
| Runs below its own maximum, port not measured | ONE end was read. Real shortfall, cause unknown                 | Recommend nothing physical. Read `upstream_name`, then the board manual. See below         |
| In a slot narrower or slower than it needs    | A better slot exists                                            | Move it; check the slot is mechanically long enough or open-ended                          |
| Capped by the mainboard                       | This port is the limit, not the card                            | Check `lsdsk slots` before proposing hardware. See below                                   |
| Held back by its controller                   | The port is slower than the drive                               | Move to a free faster port, or a better HBA                                                |
| Drives in the wrong ports                     | A slow drive holds a fast port a faster drive wants             | Swap the two drives over                                                                   |
| An attribute under the maker's threshold      | The drive's own normalised value reached the limit it publishes | Treat the drive as failing: check the backup and replace it                                |
| Link never trained                            | The link is down, or negotiated to zero lanes                   | A seating, power or connector fault. Nothing behind it can be read                         |
| Reports itself as failing                     | The drive's own overall SMART self-assessment says FAILED       | Treat it as failing now: check the backup and replace it                                   |
| Above its own temperature threshold           | Past the warning or critical limit the drive publishes          | Airflow and drive spacing. The bands are the drive's, not a fixed rule                     |
| Controller oversubscribed                     | Its drives together want more than its uplink carries           | Spread them over more controllers, or fit a wider-uplink HBA. A free port here is not free |
| Wear-out                                      | Rated endurance consumed                                        | Plan a replacement, see the thresholds below                                               |
| Reallocated sectors                           | Media degrading                                                 | Snapshot now, compare later                                                                |
| Pending sectors                               | Unreadable, awaiting a write                                    | Back up first, then rewrite or replace                                                     |
| Uncorrectable sectors                         | Data already lost                                               | Replace, restore from backup                                                               |
| Media errors (NVMe)                           | Unrecovered integrity errors                                    | Snapshot now, compare later                                                                |
| Interface CRC errors                          | Frames corrupted on the wire, resent                            | Reseat or swap the cable; the drive is not at fault                                        |
| Mixed firmware                                | Same model, different revisions                                 | Level up at the next window                                                                |

### The port was not measured

"runs below its own maximum, and the port was not measured" is a different
finding from the one below, and confusing the two is how somebody ends up
reseating a soldered-down drive. It means one end of the link was read and the
other was not, so the shortfall is real and its cause is unknown. A socket built
a generation below the device produces this reading exactly as a fault does.

Recommend nothing physical on it. No reseating, no cable, no bay, no slot-speed
override, and on Windows no elevated re-run - the port's registers are not
published there at any privilege (see above). Suggesting any of them asserts a
cause the tool explicitly did not establish.

**Read `upstream_name`, which is how this question usually closes.** It is what
the port is CALLED, carried because a platform can withhold a port's capability
and still name it. Many vendors put the width and generation in that name, so
`Intel(R) PCIe RC 060 (x4) G4` says Gen4 x4 - and a Gen5 drive at Gen4 x4 in a
Gen4 port is at its ceiling, with nothing wrong. Say where that came from: the
port's driver names it so, which is weaker than a measurement and strong enough
to act on. It is not parsed into a capability, and a name without numbers in it
tells you nothing - then send the reader to the board manual with the PCI
address.

`upstream_name` is not a column. It reaches a human reader only inside this
finding's text, as `The port is named "..."`, and a program reads it from any
controller's `--format json`. So a machine with no such finding shows it
nowhere, and asking a reader to look for a port column sends them hunting for
something that is not there.

**Two names, two sources, and only one of them follows the operating system's
language.** A CONTROLLER's name is resolved from its numeric PCI identifiers, so
it reads the same on every machine and always in English. The `upstream_name`
above is the exception: it is QUOTED from the platform, so on a German Windows
it arrives as `PCI-zu-PCI-Bruecke` and on an English one as `PCI-to-PCI Bridge`.
Nothing is misconfigured when one line is English and the other is not, and the
quoted name is still worth reading - it is the only statement about that port.

That also means lsdsk and the Windows Device Manager will disagree about what a
controller is called. Device Manager shows what the driver package calls it,
which for Microsoft's in-box drivers is a generic label like
`Standardmaessiger NVM Express-Controller`. lsdsk shows what the silicon is.
Same device at the same PCI address, named from different sources; match them by
ADDRESS, never by name, and do not tell anyone their machine is wrong.

**Width decides which way to lean.** A link at FULL width and lower speed is the
port's ceiling almost every time; seating and cabling faults cost LANES, so they
show as a width below maximum. Say which of the two you are looking at.

### Capped by the mainboard

The title names the port, not the board. Two different situations produce it and
they have opposite remedies, so read the finding's own detail before recommending
anything. It says which one this is.

- **The board has faster ports, but they are occupied.** The detail says so and
  names the generation, and the remedy is to free one. **Do not propose a new
  board**: the machine already has what the card needs. A Gen5 board with every
  Gen4 port full reads exactly like a Gen3 board until you read that sentence.
- **The board genuinely has nothing faster.** Only then name the PCIe generation
  it would need, not just "upgrade".

Either way, weigh it against demand first. When the attached drives want less
than the link carries, the ceiling costs nothing today: say that and stop. The
fix is never a new controller, which is already faster than the board.

Run `lsdsk slots` before answering. It is the only view that shows whether a
faster port exists and what is sitting in it.

### Wear

Warning at 80% of rated endurance consumed, critical at 95%. Below 80% is not
flagged and is not a reason to replace anything: a drive at 59% has consumed
somewhat over half its rated writes and is not "wearing out" in any actionable
sense. Past 100% drives usually keep working; what ends is the warranty and the
manufacturer's prediction. Pair the percentage with lifetime bytes written to
estimate how long the rest will last at the current rate.

### CRC errors are about the cable, not the drive

The one health counter that says nothing about the media. Frames were corrupted
between the controller and the drive and had to be resent, which is a cable, a
connector or a backplane slot. Never recommend replacing the drive for it. SATA
also downshifts a link that keeps erroring, so a high CRC count beside a link
running below both ends is one fault showing up twice, not two problems. A
handful can come from a single hotplug; a persistent or rising count cannot.

The size of the count does not rank two drives. Check `lsdsk trend` before
recommending physical work on either: the bigger number is often the older,
finished fault. Where the record proves a count is still climbing, the finding
already reads CRITICAL and carries the rate; where it proves the count is dead,
the finding is downgraded to a hint and says so.

### Reallocated sectors and media errors

Different things. A reallocated sector was retired pre-emptively and the data
survived. A media error or an uncorrectable sector means recovery failed. A
non-zero reallocated count is common on old drives and is not by itself a
replacement trigger; growth is. Media errors and pending sectors are more urgent
because data was lost or is at risk.

There is no universal raw count that means "act now", and picking one is
guessing: what counts as many depends on the drive's spare pool, which differs
by model. The drive already publishes its own answer. Every SMART attribute
carries a normalised value and the threshold its maker set, both shown for every
disk on the SMART page of `lsdsk tui` and in `--format json`. Judge against those: a value
still far above its threshold is a drive reporting itself healthy however large
the raw count looks, and one approaching its threshold is the drive itself
saying it is running out of margin. Quote both numbers rather than the raw count
when you justify a replacement.

## Quoting a drive

Device names move between reboots, so a work order should quote the `wwn`
column, which is what the drive itself publishes: `naa.` for SATA and SAS,
`eui.` or a namespace `uuid.` for NVMe. It stays with the drive into whatever
bay it lands in.

## Finding room, and what could move where

`lsdsk controllers` counts ports used against total per controller and what the
attached drives demand. Free ports on a controller whose uplink is oversubscribed
are not really free.

A `-` in that count is not zero, and the reason differs by controller. A SAS HBA
always answers, because it publishes a phy per lane - though a phy is not a
connector, so read the caveat under `slots` before calling a free count free. An AHCI controller answers only
where firmware publishes its ports-implemented bitmap; where it does not, the
count is dropped rather than guessed, because the kernel's port list reports the
declared number and a chipset that declares six commonly wires two. An NVMe
controller has no spare port at all: it is the drive's interface, so a free-port
count would be a category error. When the count is absent, answer the question
from `lsdsk slots` instead, which shows the PCIe ports that are free.

`lsdsk slots` is the board-level view: every PCIe port, what it is capable of,
what it negotiated, what occupies it, what that occupant needs, and a verdict.

| Verdict                    | Means                                                                  |
|----------------------------|------------------------------------------------------------------------|
| `FREE`                     | Empty, and the hardware confirmed a physical connector                 |
| `spare N GB/s`             | Occupied by a card that cannot use the port's full bandwidth           |
| `port limits it`           | The occupant is faster than the port it sits in                        |
| `full`                     | Occupant and port matched                                              |
| `in use (graphics)`        | A graphics card. Never proposed for displacement                       |
| `no connector`             | An internal port to a soldered-down device, not a slot                 |
| `empty, connector unknown` | Nothing there, and root was needed to tell whether it is a real socket |

`spare` is a measurement and appears whether or not a move is possible, so it is
not on its own an instruction to swap anything. "Swappable" means the occupant
could take the slower slot without losing anything, which is why a card showing
`spare` is a swap candidate and a graphics card never is. A move additionally
needs a real connector at both ends and mechanical clearance, which is why an
unprivileged run never proposes one.

Read the two views together before moving anything. A `FREE` port is not by
itself a safe destination: check in `lsdsk controllers` that the controller
behind it is not already oversubscribed, or you move the bottleneck rather than
removing it.

Slot numbers and connector detection need root or Administrator. Without them the
`slot` column reads `-` and `FREE` is withheld, because an internal port to a
soldered-down device is empty in exactly the same way as a real socket.

## lsdsk does not go online. You can

Every limit below is the TOOL's, not yours. It reads hardware and makes no
network request by design, so it never reports a figure it did not measure. You
have a browser and `curl`, and the things it cannot know are published. Look them
up rather than reporting a dash back to somebody who asked a real question.

lsdsk gives you the exact search keys: the board name in the banner, from DMI;
the controller model in `lsdsk controllers`; the drive model and firmware in
`lsdsk disks`.

| Question lsdsk leaves open                                                                                    | Where the answer is                                          |
|---------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| How many SATA ports, how many M.2, which are Gen4 or Gen5                                                     | The board's spec page or manual                              |
| Whether slot #N is an M.2 socket or a card slot, and its silkscreen label                                     | The board manual's layout diagram                            |
| Which slots share lanes                                                                                       | The board manual, usually a footnote under the storage table |
| How many drives an HBA or RAID controller really drives, and whether its ports are direct or need an expander | The controller's product brief or datasheet                  |
| Whether a RAID controller is in IT/HBA mode or RAID mode, and what its cache and BBU do to write behaviour    | The controller's manual, then its own management tool        |
| A drive's rated endurance in TBW, to turn a wear percentage into a date                                       | The drive's spec sheet                                       |

Fetch it directly when you know the vendor URL, and search when you do not:

```bash
curl -sL https://vendor.example/products/motherboard/<board> | sed 's/<[^>]*>//g' | grep -iA3 sata
curl -sLI https://vendor.example/doc/<controller>-DS   # check it exists before fetching a large PDF
```

A web-search or fetch tool does the same job and handles PDFs better; use whichever you have.

**Lane sharing is the one worth looking up every time.** Boards routinely disable
SATA ports when an M.2 socket is populated, or drop a slot to x2 when its
neighbour is filled. That single footnote explains a whole class of lsdsk
findings: a port reporting fewer lanes than the slot is rated for, or SATA ports
that are simply absent. lsdsk sees the result and cannot see the cause.

**A SAS port count is phys, not connectors, so check it against the card.** The
count is the number of phy objects the driver publishes, which is right on many
cards and too high on some: a 9500-16i, a sixteen-lane card, publishes twenty-one
with no expander attached, so a free count derived from it overstates what you
can physically plug in. The model name usually carries the real number, and the
datasheet always does. Look it up before promising somebody free ports, and say
the count came from the card's specification rather than from the machine.

**Expect some vendor sites to refuse an automated fetch.** Supermicro and several
others answer 403 to `curl`. Do not quietly substitute a retailer listing and
present it as the manual: name the source you actually used, say the primary one
was unreachable, and hand over the URL so the reader can open it themselves.

**Keep the two apart when you answer.** Say which numbers lsdsk measured on this
machine and which you read from a manual, and cite the source. They fail
differently: a measurement is true of this box and a manual is true of the model,
so a board revision, a BIOS option or a populated shared slot makes them
disagree. When they do, lsdsk is describing what is actually there. Prefer the
vendor's own page over a review or a retailer listing, and say so when you had to
settle for one of those.

## What it cannot tell you, so do not assert it

**It cannot tell an M.2 socket from a PCIe card slot.** No readable source gives
the form factor: the firmware slot table is the only one that carries it, and on
real boards it routinely lists no M.2 socket at all and names ports that do not
exist. Never infer the form factor from the width, because an x4 port is as
likely one as the other. `lsdsk slots` reports the board's own slot number
instead: match that against the mainboard manual, which is also how you turn a
PCI address into a slot you can point at.

It reads hardware, not configuration or physical layout. It does not know the
RAID or ZFS layout, which pool a disk belongs to, whether a drive is a boot
device or a cache, which physical bay holds it, how old a SATA drive is unless
it reports power-on hours, or whether newer firmware exists. It has no network
access, so it never knows a drive's rated endurance in terabytes written or an
OEM rebrand name. When a recommendation depends on any of those, ask rather
than infer, and map the device name to a bay before issuing a work order.
