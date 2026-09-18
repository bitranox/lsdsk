# Checklist - the topology section is the PCI fabric

`lsdsk topology` draws the PCI fabric root-down, draws the LEAST of it by
default, and puts SYMBOLS rather than figures in the hop columns where no
register was published. The skill described a disk-to-controller tree and
carried none of the three, so it sent a reader to a wrong conclusion rather
than to none: a `-` read as a dead link is a fault report about working
hardware, and a default that omits the graphics card reads as a tool that
cannot see it.

## Change

- A new `## Reading lsdsk topology` section: the board line, the three
  densities with the shipped default named, the two hop symbols with their
  legends, the `capable`/`running` pair read against the port above, and the
  marker column.
- The page listing and the `topology` command line say PCI fabric rather than
  controller tree.
- The global-option list gains `--tree-density`, and its after-the-subcommand
  count goes from three to four.
- No rule, threshold or finding text moved. Frontmatter untouched.

## RED, GREEN

A behavioural RED is not available here and was not faked. `redcheck
--corpus-cascade` reports INHERITED COVERAGE, STRONG: this repo's own
`CLAUDE.md` already teaches the hop symbols, so a dispatched agent answers
from the cascade rather than from the scenario, and the baseline cannot fail
honestly. The route taken is the one prescribed for that case - a text check
of the artifact, which inherited context cannot reach - in the shape the repo
already uses for link figures.

- [x] RED: `tests/test_skill_describes_the_fabric_view.py` fails on the
      pre-change skill with three findings: the default's density note absent,
      both hop legends absent, and no line showing the device column header.
- [x] RED for the option list is separate and named its own omission:
      `the skill's global-option list omits options the CLI accepts:
      ['--tree-density']`.
- [x] RED is not vacuous: `test_the_producers_still_write_something_to_look_for`
      is the control and passes, so each expected string is non-empty and the
      two hop legends still differ from each other.
- [x] Every expected string is taken from its PRODUCER (`tree.density_note`,
      `theme.hop_legend`, `tree.DEVICE_COLUMNS`, the CLI group's own params),
      never typed into the test, so a reworded producer fails until the skill
      follows.
- [x] GREEN: the new module and `test_skill_quotes_output_the_tool_produces`
      both pass, and the full suite is 1437 passed, 8 skipped.

## Skill gaps

- [x] The first option-list check passed against the very omission it was
      written for, because the option is named in the new section and a
      whole-document search found it there. Closed by scoping the check to the
      paragraph a reader consults.
- [x] A claim that a `-` may fill in when the same capture is read as root was
      removed rather than shipped. It was not measured, and Windows publishes
      no PCIe link capability for a bridge by any path, so the sentence would
      have sent a reader looking for a privilege fix that does not exist. The
      text now states what each symbol says about the EVIDENCE, which is the
      part that is established.
- [x] Device counts per density are deliberately absent. They change with a
      recapture and nothing in the skill would catch it; the RULE is stated
      instead.
- [x] None undecided.

## Quality

- [x] Present tense, no session narrative, no provenance.
- [x] No address, MAC, hostname or path added. The sample is committed-fixture
      output, and the only host name in it is the fixture's own.
- [x] The sample's link figures pass the sibling guard, so every figure quoted
      is one the formatter produces.
- [x] Frontmatter untouched, so no routing keyword moved.
