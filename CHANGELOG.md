# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

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

[1.0.0]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.0
