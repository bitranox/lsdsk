# Checklist - the slot-verdict table lists every verdict the column prints

`lsdsk slots` prints one of eight verdicts and the table enumerated seven. The
missing one is `in use`, which `report.slot_verdict` returns for an occupied
port whose capability or whose occupant's need was not read. A verdict table is
a CLOSED vocabulary, so an unlisted value reads to an agent as an anomaly to
escalate rather than as an ordinary answer.

## Change

- One row, placed beside `in use (graphics)`, the verdict it is one parenthetical
  away from.
- It says what the value means and where it is the rule rather than the
  exception: Windows, which publishes no link registers for a bridge, a fact the
  skill already states elsewhere.
- No other row, rule, threshold, exit code or finding text moved. Frontmatter
  untouched.

## RED, GREEN

The lesson under test is a FACT ABOUT THIS TOOL'S OUTPUT, so the evidence is a
text check of the artifact rather than a dispatched baseline: an agent asked
which verdicts `slots` prints would answer from this repo's own cascade.

- [x] RED: `test_the_skill_lists_every_verdict_the_slots_table_can_print` fails
      on the pre-change skill with `printed but not documented: ['in use']`, and
      `documented but not printed: []`, so the walk and the table agreed on the
      other seven and the one difference is the omission.
- [x] The oracle is WIDER than the fix: the expected set is read off
      `report.py`'s own AST, following `return` statements into the helper
      `slot_verdict` hands off to, so a verdict added later fails the test
      whether or not anyone thought to drive it. A list of slots written by hand
      could only have found the branches its author already knew.
- [x] RED is not vacuous: the test requires at least seven verdicts from each
      side, so a moved table heading or a renamed function fails as an unchecked
      claim rather than passing on two empty sets.
- [x] The claim was re-measured over the committed captures before the edit:
      windows-ahci prints `in use` on both of its occupied ports, and none of
      the 53 ports across the four Linux captures prints it.
- [x] GREEN: both skill test modules pass, and the full gate is green.

## Skill gaps

- [x] The row says "the rule on Windows" rather than giving a count. A count
      taken from one committed QEMU capture would read as a property of the
      platform; the mechanism, that no link register is published for a bridge,
      is what a reader can check against their own output.
- [x] `spare N GB/s` is the one row whose text is a template rather than a
      literal, and the test normalises the f-string to that same `N` form. If a
      second computed verdict appears, the normalisation is the part to re-read.
- [x] Whether `in use` should carry a style of its own is a rendering question
      for the report, not a documentation gap; left open deliberately.
- [x] None undecided.

## Quality

- [x] Present tense, no session narrative, no provenance.
- [x] No address, MAC, hostname or path added.
- [x] Frontmatter untouched, so no routing keyword moved.
