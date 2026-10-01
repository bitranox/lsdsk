# Checklist - a heading per root complex

## The change this documents

The topology tree draws a heading row for every root complex - `root complex
0000:00`, or `no bus address` for devices the platform published no address for -
with that root complex's devices one level below it, on every machine. Before, the
devices of every root complex hung straight off the board line, so a second root
complex's devices continued in a column the first one had closed.

## Change

- The quoted topology block carries the heading row and the two-column shift every
  row below it now has (the registered command's own output).
- A paragraph after the board-line one: what the heading names, that it is not a
  device, that a blank there is no reading of any kind, that the interactive view
  does not select it, and that a single root complex gets one too.
- The hop-column paragraph says a device directly under a heading has no port above
  it to read against.
- No frontmatter, no field list, no exit code moved.

## RED

- [x] `redcheck --corpus-cascade` on the worktree reported STRONG inherited coverage
      (the gitignored lsdsk CLAUDE.md shares the topology vocabulary), so the
      evidence is a text check of the artifact plus the behavioural arm below, read
      as supporting rather than decisive.
- [x] Text check: the registered quoted-block test
      (`test_a_quoted_block_is_output_the_tool_really_produces[skills/lsdsk/SKILL.md:showing storage ...]`)
      failed on the passage as it stood, because the tool now prints the heading row.
- [x] Retrieval probe (text-only agent, the topology passage as it stood, a real
      two-root excerpt): it reached the right reading by inference, but for the
      question about the heading line's empty columns it could quote NONE - its
      gaps said the documentation is silent on root-complex label lines.

## GREEN

- [x] Every test that reads SKILL.md, found by grepping `tests/` for the filename:
      144 passed.
- [x] Same probe on the new passage, with the heading question required to be a
      direct quote or NONE: it quoted "The heading is not a device: it carries no
      link and no hop figures, so a blank there is not a reading of any kind, and
      the interactive view does not select it." It excluded both headings from the
      device list and placed 0000:00:01.0 directly under its heading with no port
      above it, quoting the new parenthesis.

## REFACTOR

- [x] GREEN diffed against RED both ways: the device list, the disk row excluded as
      a nested table, and the depth chain appear in both; nothing the baseline
      produced is missing.
- [x] Gaps declined: the excerpt's own `...` hides rows no text can name, and the
      question's "the line for 0000:ff" is loose wording in the scenario, which the
      probe resolved by answering both readings.

## Deployment

- [x] No address, host or path from a real machine beyond the PCI addresses of
      committed captures' sample rows.
