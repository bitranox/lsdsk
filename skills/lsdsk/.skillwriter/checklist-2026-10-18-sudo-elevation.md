# Checklist - the interactive view asks for root through sudo on Linux and macOS

## The change this documents

The interactive view no longer asks for elevation on Windows only. A non-elevated bare `lsdsk` or
`lsdsk tui` on Linux or macOS asks for root through sudo (pwshpy's `ps.elevate`, which runs
`sudo -v` and then the copy) and, if sudo grants it, restarts as root in the same terminal, handing
back the copy's exit code. A refused prompt, a Ctrl+C at it, a wrong password and a user sudo does
not allow all come back as one DECLINED outcome, drawn as "sudo did not grant root (refused, a wrong
password, or not allowed)". It does not ask on a Linux machine whose `/sys/block` lists `sd*` or
`nvme*` disks with no node for any of them in `/dev` (`src/lsdsk/adapters/hw/elevation.py`,
`src/lsdsk/adapters/hw/linux/reader.py::disk_nodes_reachable`,
`src/lsdsk/adapters/render/report.py::elevation_note`).

## Change

- The opening paragraph: the one process lsdsk starts is itself, through UAC on Windows and sudo on
  Linux and macOS, not "the interactive view on Windows".
- The privilege section's elevation paragraph: rewritten per platform - where the copy runs (a new
  window, the same terminal), that a refusal, a wrong password and a disallowed user give one line,
  that Ctrl+C at the prompt is a refusal, that `--no-elevate` draws a line saying the run does not
  ask, the container case, and that macOS asks although the root copy cannot read hardware. No
  frontmatter changed.

## RED

- [x] Route: a text check of the skill file, not a behavioural probe. The repo's own CLAUDE.md
      already states the new behaviour, so a dispatched agent inherits it and would answer correctly
      in both arms; the RED could not fail honestly.
- [x] Three tests added to `tests/test_skill_describes_elevation.py` failed on the unchanged skill:
      the elevation paragraph said "never asks off Windows" and named neither sudo nor "same
      terminal"; it named no container; the opening said "the interactive view on Windows
      relaunches".
- [x] The same three re-run against the unchanged skill after the edit (edit stashed): still fail,
      so they are not satisfied by the test change that collapses wrapped whitespace.

## GREEN

- [x] Every test in `tests/test_skill_describes_elevation.py` passes on the edited skill.
- [x] Text-only probe (opus, inert probe type) given the new paragraph alone, six questions (a
      non-root Linux start, a mistyped password, a container, a script on a pseudo-terminal,
      `sudo lsdsk`, a printed subcommand): all answered from quoted sentences. Run for its gaps list,
      since the inherited context makes a pass weak evidence.
- [x] Its gaps, closed: which disks decide the container case (now `sd*`/`nvme*` in `/sys/block`
      with no `/dev` node); whether the never-asks cases draw a `NOT ELEVATED` line (now: only
      `--no-elevate` / `auto_elevate = false` does, saying the run does not ask); what "reads the
      same" meant (now: one line, because sudo answers them alike); Ctrl+C at the prompt (now named
      as a refusal); macOS read as contradicting the container rule (the asking there is a user
      decision; the text now says so, see the quote-back below).
- [x] Its gaps, declined: what `lsdsk report` prints and when the view opens (both defined elsewhere
      in the skill, which the probe was not shown); the exit code after an unelevated fallback (the
      opening section: the exit code is the findings' in both views); sudo not installed and other
      relaunch failures (covered by "the relaunch fails", which draws the FAILED line with pwshpy's
      reason); where `display.auto_elevate` is set (the skill's `config-deploy` lines).
- [x] Quote-back on the touched points (a direct quote or NONE): Ctrl+C as a refusal, one line for a
      refusal, wrong password and disallowed user, no line in a container, and the container's disk
      test came back quoted; "is asking on macOS deliberate" came back NONE, and whether the cost
      sentence follows the `--no-elevate` line and the mixed-node case were reported as gaps. The
      text now says "On macOS it asks deliberately", "again followed by what that cost", "one node is
      enough to ask, and a machine listing no `sd*` or `nvme*` disk is asked"; a second quote-back on
      those four returned a direct quote for each.
- [x] Second quote-back's gaps, declined: "any subcommand but `tui`" read as excluding bare `lsdsk`
      (the paragraph's first sentence, not in that excerpt, names bare `lsdsk` and `lsdsk tui`); why
      `vd*` and the like are not in the container test (a judgement the code makes, stated as a rule);
      what the line says on macOS (the same DECLINED line as Linux, both being sudo).

## REFACTOR

- [x] RED vs GREEN diffed both ways: the RED was a text check with no answers to lose.

## Quality

- [x] No address, hostname or path from a real machine added; no session narrative.
