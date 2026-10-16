# Checklist - the interactive view elevates itself on Windows; a SATA link needs Administrator there

## The change this documents

Two behaviours of the code the skill described wrongly or not at all:

- On Windows a SATA drive's `disk` and `link` figures come only from ATA IDENTIFY, which needs
  Administrator, so a non-elevated run shows `-` for both; `privilege_note` now names "the SATA link
  (disk and link columns)" (`src/lsdsk/adapters/render/report.py`, `SATA_LINK_NEEDING_IDENTIFY`).
- A non-elevated Windows start of bare `lsdsk` or `lsdsk tui` asks for Administrator first and, if
  allowed, relaunches itself elevated in a new window through pwshpy's `ps.elevate`, the original
  window returning the copy's exit code; declined, failed or turned off, the view opens unelevated
  with a `NOT ELEVATED` line above every page (`src/lsdsk/adapters/hw/elevation.py`,
  `src/lsdsk/adapters/cli/commands/scan.py::ask_before_the_view`). `--no-elevate` and
  `display.auto_elevate = false` turn the asking off; `--elevated-relaunch` is the copy's internal
  marker.

## Change

- The opening paragraph: "no subprocesses" replaced by "shells out to nothing", naming the one
  process it starts, itself.
- The global-option list: `--no-elevate`, and one line naming `--elevated-relaunch` as internal.
- The privilege section: link speeds read unprivileged except a SATA drive's on Windows; "Five
  things" with a row for the Windows SATA link; a paragraph on the interactive view asking for
  Administrator, what each outcome does, that nothing is remembered, where it never asks, and what an
  unattended caller does. No frontmatter changed.

## RED

- [x] Route: a text check of the skill file, not a behavioural probe. This machine's memory index and
      the repo's own CLAUDE.md state the new behaviour, so an agent would answer from inherited
      context in both arms and the RED could not fail honestly.
- [x] `tests/test_skill_describes_elevation.py` (new) failed on the unchanged skill: "no subprocesses"
      present; `SATA_LINK_NEEDING_IDENTIFY` absent; `NOT ELEVATED`, `auto_elevate`, `--no-elevate`
      absent; the relaunch marker named nowhere.
- [x] `test_the_skill_names_every_option_the_cli_offers` and
      `test_the_skill_names_every_global_option_the_cli_declares` failed on the unchanged skill (the
      two new options unnamed).

## GREEN

- [x] Every test that reads SKILL.md passes.
- [x] Text-only probe (opus, inert probe type) on the new passages, seven questions (a non-elevated
      Windows SATA dash, the same on Linux, the exit code after an accepted prompt, a refused prompt,
      a printed command, the internal marker, the two ways to never be asked): all answered correctly
      with the governing sentence quoted. A probe that inherits the same context proves little by
      passing; it was run for its gaps list.
- [x] Its gaps, closed: "printed command" was undefined out of context (now "any subcommand but
      `tui`"); whether a refusal is remembered was unstated (now "Nothing is remembered: it asks again
      on every start").
- [x] Its gaps, declined: the permanent location of `display.auto_elevate` (the skill's
      `config-deploy` lines cover where a user config file goes); the exit code after a refusal (the
      opening section says the exit code is the findings' in both views); how JSON shows the shortfall
      (the `skipped` section); a Linux link reading `-` and the "anything below" wording (unchanged
      text outside this change).
- [x] A sentence of the first draft, "a script never meets the prompt", contradicted the skill's own
      warning about callers that hand their child a pseudo-terminal; replaced before the probe ran.

## REFACTOR

- [x] RED vs GREEN diffed both ways: the RED was a text check with no answers to lose.

## Quality

- [x] No address, hostname or path from a real machine added; no session narrative.
