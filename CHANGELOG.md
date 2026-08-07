# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

## [1.0.3] - 2026-08-07

Mostly about what the output looks like and who it is for.

### Changed

- A bare `lsdsk` opens the interactive view at a terminal and prints the page
  everywhere else. The whole machine on one page is more than a reader takes in
  at once, but a full-screen application cannot run into a pipe, so the switch is
  what the output IS rather than a flag. Every existing pipe, redirect, CI run
  and agent keeps the printed page, and the exit code is the findings' in both,
  so `lsdsk; echo $?` means one thing regardless.

### Added

- `lsdsk --report` prints the whole-machine page whatever the terminal looks
  like. The switch above reads the terminal, and there is one thing it cannot
  see: a caller that allocates a pseudo-terminal for a program. IPython runs
  `!cmd` under pexpect, so a notebook cell presents a terminal on both ends and
  is indistinguishable from somebody sitting at a shell; `script`, expect and
  pty-allocating job runners do the same. The interactive view then waits for a
  keypress that has no keyboard behind it. Detection cannot be made to cover
  this, so the flag is the way out, and it is what anything unattended should
  say. Given before a subcommand, where it has no view to choose, it is refused
  rather than silently ignored.
- The palette is readable on a light background as well as a dark one. Measured
  as WCAG contrast against four real terminal backgrounds, the previous hint and
  ceiling colour scored 1.7:1 and the warning colour 2.4:1 on a light background,
  where the floor for large text is 3.0. Two causes compounded: the faint
  attribute, which terminals implement by blending toward the background and
  which therefore removes the contrast it is asked to provide, and naming a
  colour the terminal resolves through its own palette. Nothing is faint any
  more, including column headers and whole columns of values that were dimmed
  wholesale, and every colour is now the most legible member of its hue at
  4.2:1 worst case.
- A trend refusal no longer reports an expectation that rounded away. "only 0.0
  were due" stated that none were due and left "only" contradicting its own
  number; a drive with one lifetime CRC error in 42278 hours predicts 0.000024 of
  one across an hour. Zero elapsed time and a real but sub-unit expectation now
  say what they each mean.

### Added

- The version rides in the report banner and the interactive view's title. This
  output gets pasted into tickets, where the first question asked of a surprising
  reading is which build produced it.

### Fixed

- The bundled skill teaches the unmeasured-port finding that 1.0.2 introduced,
  and no longer tells a Windows user to re-run elevated to reveal a port's
  capability - the registers are not published there at any privilege, so the
  second run returns the same dashes.

## [1.0.2] - 2026-08-07

Corrects a false diagnosis. On Windows, a drive that was doing everything right
could be reported as faulty, with instructions to reseat hardware that is
soldered down and to check a cable that does not exist.

### Fixed

- A PCIe link is no longer called a fault when only one end of it was measured.
  Where the port's capability could not be read, the tool took the DEVICE's own
  maximum as the port's, so any drive faster than its socket looked like a
  negotiation failure. Reported from a Gen5 NVMe drive in the CPU-attached Gen4
  x4 M.2 socket of a Raptor Lake board: it was running exactly as fast as that
  socket allows, and was told to reseat the card, check the riser and cabling,
  and look for a BIOS slot-speed override. Such a shortfall is now reported as
  real but unattributable, in yellow, and the remedy no longer names a cause.
- Windows reports the driver behind each controller. It was never asked for, so
  every controller showed "-" where Linux shows the bound kernel module. The
  column now reads stornvme, storahci or iaStorVD, which is the same thing the
  Linux column means.

### Added

- Controllers carry the name of the port above them, and the structured output
  exposes it as `upstream_name`. Windows publishes no link registers for PCIe
  bridges - measured on one board: 8 bridges, none with a link speed, and no
  registry, WMI or user-mode API that has them - but it does name the port, and
  a name like "Intel(R) PCIe RC 060 (x4) G4" answers what the missing registers
  left open. It is quoted, never parsed into a capability: it comes from a
  driver package rather than from the hardware, and only some vendors put the
  width and generation in it.

## [1.0.1] - 2026-08-07

A maintenance release. It behaves exactly as 1.0.0 does: the only change inside
the package is which line an import sits on, and a sentence in a docstring.

What it really carries is the first green CI run. The outage on release day
cancelled every job before it executed a step, so 1.0.0 shipped on local
evidence alone. Running the matrix afterwards found nine failing
tests across Windows and macOS plus two modules Windows could not import, all
of them in the tests rather than the tool - which is consistent
with 1.0.0 installing and running correctly from PyPI throughout.

### Fixed

- The Linux reader module can be imported on a machine without ``fcntl``. It
  imported that Linux-only module at file scope, so on Windows the module could
  not be imported at all. No released behaviour depended on it: the snapshot
  adapter already imported the reader lazily and only on Linux, so the CLI never
  reached it. It made the module unreachable to tooling, and unimportable by
  anything that walks the package.
- The ``read_text_bounded`` docstring no longer shows a path that only a POSIX
  machine would print.

### Changed

- Tests no longer assume they are running on Linux. Nine failures came from that
  assumption: POSIX file modes, ``os.geteuid``, ``XDG_CONFIG_HOME``,
  ``Path.home()``, path separators in comparisons, and a captured pipe decoded
  with the runner's codepage rather than a stated encoding. The suite now runs on
  Python 3.11 through 3.14 across Linux, macOS and Windows.
- Tests no longer read the developer's own counter history or ``.env``. Both made
  a result depend on the machine it ran on, and one of them silently supplied the
  secret that another test needed in order to prove anything at all.

## [1.0.0] - 2026-08-06

First release. Everything below is what it does.

### Added

#### What it reports

- Disks grouped by the controller they hang off, with every interface link
  graded against what both ends of it can do: the port, the drive, and what the
  two negotiated, shown as three numbers rather than one verdict.
- SMART wear, temperature and error counters, judged against the thresholds each
  drive publishes for itself rather than against a fixed rule. An attribute the
  drive's own maker marks as failing is a critical finding.
- A mainboard slot view: every PCIe port, its capability, what occupies it, what
  that occupant needs, and which ports are free.
- The mainboard named from DMI, which is what makes the placement advice
  actionable.
- Controller oversubscription, where the drives on a controller together want
  more than its uplink carries.
- Findings carry the measurement that produced them and what to do about it, and
  severity is graded against what the machine can actually give: a link below a
  device's own maximum is a fault only when something better is available.

#### How you drive it

- A bare `lsdsk` renders the whole machine on one page; `topology`,
  `controllers`, `disks`, `health`, `smart`, `findings`, `slots` and `trend` are
  each one section of it, under the same names as the interactive pages.
- An interactive view (`lsdsk tui`) keyed like the `*top` family.
- `lsdsk snapshot` captures the raw reading, and `--replay` renders a capture
  from any machine through the identical decode and diagnosis path. `snapshot`
  itself always captures the machine it runs on and refuses `--replay` rather
  than silently ignoring it.
- `--format json` emits a validated envelope carrying `ok`, `command`, `data`
  and `skipped`, on every command that produces data, so one reader handles them
  all without branching on shape. `tui`, `fail` and `logdemo` have no structured
  mode, having no data to structure.
- Exit codes a caller can branch on, documented and fixed: `0` and `1` from the
  findings of a reporting command, `2` for a usage error, `13`, `22` and `78`
  for the three ways a run cannot proceed.

#### Counter history

- Counter history, so an error count becomes a rate. A drive keeps the lifetime
  total in its own non-volatile table and never keeps the past, so the total
  cannot say whether a fault is live or years old. `lsdsk trend` says which,
  every finding carries the measurement, and severity follows: a counter proved
  to be rising is escalated and one proved to be quiet is stood down.
- Rates are measured per power-on hour of the drive itself, so they survive
  clock steps, timezones and a machine that spends most of its time off.
- Silence counts as evidence only where the drive's own lifetime rate says
  errors should have appeared, so a slow trickle over a short span reports that
  it cannot tell rather than implying the drive is fine.
- `lsdsk record` stores one reading and prints nothing, for a timer; with
  `--format json` it reports what it stored. Any ordinary run also records once
  the drives' clocks have moved on, so the history feeds itself.
- The store is written atomically, is owner-only, and is capped per drive. A
  store that cannot be read is left exactly as it is rather than replaced,
  because unlike a capture it cannot be rebuilt from the hardware.
- As root the store lives at `/var/lib/lsdsk/history.json`, and per-user
  otherwise. Reading the counters needs root, so on a server the root path is
  the one that fills.

#### Platforms and safety

- Linux and Windows, issuing no subprocesses and making no network requests.
  Windows support is `ctypes` alone: no WMI, no PowerShell.
- Anywhere else runs `--replay` against a capture taken on a machine it supports.
- Text a device reports is stripped of control characters where it is decoded,
  so a model number cannot carry escape sequences into a terminal.
- A capture handed to `--replay` and a store handed to `--history-file` are
  refused above 64 MB, from the directory entry rather than after reading, so
  pointing either at a disk image fails immediately instead of spending the
  memory first. The largest capture from a real machine is 148 KB for 19 drives.
- Secrets in configuration are redacted by what a key's name means rather than
  by how it was spelled, in the human and the structured view alike.
- It runs unprivileged and says what that costs, naming the columns it could not
  read instead of reporting them as zero.

#### Configuration

- `[thresholds]`, `[display]` and `[history]` sections: every value the tool
  judges or lays out by is a documented configuration key, deployed with
  `lsdsk config-deploy` and overridable for one run with `--set`.
  Specification-fixed values, such as register offsets and the Kelvin offset,
  stay fixed.
- Every documented key is covered by a test that sets it two ways and requires
  the tool to behave two ways, so a key that reads as configurable is.

#### For agents

- A Claude Code skill teaching how to read the output and what to do about each
  finding, including that a large error count ranks nothing until the rate is
  known, and that a normal run records one reading.

[1.0.3]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.3
[1.0.2]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.2
[1.0.1]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.1
[1.0.0]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.0
