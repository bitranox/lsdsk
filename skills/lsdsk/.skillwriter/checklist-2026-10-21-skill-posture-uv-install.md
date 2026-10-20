# Checklist - the skill's posture sentence names the consent-gated uv install

## The change this documents

lsdsk's advertised "shells out to nothing and has no network" guarantee gained a second opt-in
exception: when a reader accepts the desktop-shortcut offer and `uvx` is missing, lsdsk can run the
official uv installer after an explicit consent prompt. CLAUDE.md "## What this tool is" already
states this; the usage skill's opening posture sentence still named only the Graylog log sink and
the elevation relaunch, so it was factually stale. This pass corrects that one sentence. The earlier
install-shortcut checklist (2026-10-20) deferred exactly this prose to "the same skill's own later
passes"; this is that pass.

## Change

- `skills/lsdsk/SKILL.md`, opening paragraph: the posture sentence now reads "shells out to nothing
  and makes no network request, bar two opt-in paths" and names both the Graylog log sink and the
  consent-gated uv install (`adapters/uv_bootstrap.py`, the one module allowed a subprocess, held by
  `tests/test_no_subprocess_or_network.py`), then "Apart from that installer, the one process it ever
  starts is itself" for the elevation relaunch. Wording mirrors CLAUDE.md's canonical phrasing.
- No frontmatter change; no command or option added or removed; no tool output quoted.

## RED

- [x] Evidence is a text check of the skill FILE, not a behavioural probe: the lesson is already in
      this machine's CLAUDE.md cascade ("bar two opt-in paths"), so a baseline-probe would inherit it
      and the behavioural RED would be contaminated. `git show HEAD:skills/lsdsk/SKILL.md` posture
      sentence read "shells out to nothing and has no network unless you enable the optional Graylog
      log sink ...; the one process it ever starts is itself, when the interactive view relaunches
      elevated", with no mention of the uv installer. The skill file was missing the fact.

## GREEN

- [x] `grep -c 'official uv installer after a consent prompt' skills/lsdsk/SKILL.md` = 1; the old
      wording `has no network unless you enable the optional` = 0.
- [x] `test_skill_describes_elevation.py`, `test_skill_describes_the_fabric_view.py`,
      `test_skill_quotes_output_the_tool_produces.py`, `test_the_skill_agrees_with_the_package.py`
      all pass: 32 passed. The edit adds one posture clause, quotes no tool output, and names no CLI
      option, so the agrees-with-the-package and no-invented-option checks stay green.

## Gaps the runs reported, and their decision

- Closed: the posture sentence is now factually consistent with CLAUDE.md, README.md and SECURITY.md.
- Declined, out of scope here: the full interactive-offer walkthrough and the uvx launch-target
  command belong in the usage skill's command/flow sections and INSTALL.md, not this one-sentence
  posture correction.

## Quality

- [x] No frontmatter change (name and description untouched; router input unchanged).
- [x] No em-dash or AI tell; ASCII hyphens only, matching the surrounding sentence.
- [x] The named paths exist: `src/lsdsk/adapters/uv_bootstrap.py` and
      `tests/test_no_subprocess_or_network.py`.
- [x] No address, hostname or machine path added; the two references are in-repo module/test paths.
