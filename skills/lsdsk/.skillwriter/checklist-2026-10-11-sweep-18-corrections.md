# Checklist - sweep-18 and chained-chipset corrections to the skill

## The change this documents

Sweep 18 and the chained-chipset fix made four statements in the skill untrue or incomplete:

- the hop-column and panel marker lists did not explain the new register-default mark `*`
  (`Gen1x1*`, legend `* = register default, not the real link`), which the controllers and slots
  tables, the topology tree and the detail panel now draw;
- the unread-disk-interface line was said to be printed by "the human header"; every human page
  that lists disks now prints it;
- the floor-reading guidance said a figure carries its worth, `Gen1x1 (0.25 GB/s)`; a floor link
  lsdsk has recognised now reads `Gen1x1*` with no bandwidth, and on a board with two chipset chips
  daisy-chained the first chip's ports can show no real link because its only fast port leads to
  the second chip, a bridge Windows publishes no link for;
- the floor hint's third reading was "a third device there has a real link"; it may now also lie
  beneath one of that switch's ports.

## Change

- New paragraph after the `<=` ceiling paragraph: what `Gen1x1*` is, where it is drawn, that it
  carries no bandwidth, and that `--format json` keeps the raw register values
  (`current_speed_gtps` 2.5, `current_width` 1) which are not a ceiling either.
- Envelope paragraph: "every human page that lists disks says" in place of "the human header says".
- Floor reading 2: the `Gen1x1*` form named before the priced form; match on the figure.
- Floor reading 3: the daisy-chained case and where its real links are.
- Recognition paragraph: "a third device there, or anywhere beneath one of that switch's ports".
- No frontmatter changed.

## RED

- [x] `test_the_skill_explains_every_marker_a_view_can_draw` extended with `REGISTER_DEFAULT` and
      tightened: the symbol must open a code span and be followed by a blank or the span's close,
      because a bare backtick-star also begins every bold run that follows a code span, so the old
      substring check passed for `*` on a skill that never mentions it. On the old skill the four
      existing markers pass the strict check and the test fails naming only `['REGISTER_DEFAULT']`.
- [x] The redcheck cascade route: the lesson is in the tool's own output and this repo's docs, so
      the behavioural arm would inherit it; the text check against the skill file is the evidence.

## GREEN

- [x] Every test file that reads `skills/lsdsk/SKILL.md` (8 files, 171 tests) passes on the edited
      skill, the marker test included.
- [x] The JSON sentence was checked against a real envelope from the `windows-chipset-switch`
      capture: the controller's `link` carries `current_speed_gtps` 2.5 and `current_width` 1.

## REFACTOR

- [x] Quote-back on the new text (three questions: is the starred controller limited to 0.25 GB/s;
      what does a script see and may it treat it as a ceiling; where are the real links on a
      daisy-chained board). Each answer was a direct quote of the governing sentence.
- [x] Skill gaps the reader reported, each decided:
      - "a SATA controller is one of those functions" - declined, the surrounding paragraph names
        the SATA and USB functions integrated into the chip;
      - "how to tell the first chip from the second" - declined, the slots paragraph already says
        ports on one switch share a bus and which row is the bridge it hangs behind;
      - "what a script sees for a real measured link" - declined, out of scope for this change.

## Quality

- [x] No frontmatter change; no address, MAC, hostname or path from a real machine added.
- [x] No narrative or provenance in the skill text.
