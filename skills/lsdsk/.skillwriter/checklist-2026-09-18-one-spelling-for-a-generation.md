# Checklist - one spelling for a PCIe generation

`lsdsk` writes a PCIe link figure in the marketing form everywhere: `Gen3x4`,
`Gen1x1 (0.25 GB/s)`. The skill quoted the decimal spelling (`1.0x1`) in the
PCIe-floor rule, so a reader matching their own output against that rule found a
string the tool does not print and concluded the rule did not apply to them.

The skill was also inconsistent with itself: the floor rule quoted `1.0x1` while
the two rules directly under it quoted `Gen1x1` for the same floor.

## Change

- The PCIe-floor rule quotes `Gen1x1` and `Gen1x1 (0.25 GB/s)`.
- Nothing else. No rule, threshold, command or routing line moved.

## RED, GREEN

The failing test is the repo's own guard, which is mechanical and executable, so
it stands in for a pressure scenario: a quoted literal is either the string the
formatter produces or it is not, and no judgement is involved.

- [x] RED: `pytest tests/test_skill_quotes_output_the_tool_produces.py` fails
      before the edit with `line 644: the skill quotes '1.0x1'; the tool prints
      'Gen1x1'`. The guard names the line and both strings.
- [x] RED is not vacuous: `test_the_skill_quotes_some_link_figures_at_all` is the
      control and passes, so the pattern is still matching figures.
- [x] GREEN: the same module passes after the edit.
- [x] The guard reads the formatter rather than a fixed list of lines, so it
      catches the next drift as well as this one.

## Skill gaps

- [x] None undecided. The only judgement the change could have raised - which of
      the two spellings a reader recognises - is settled outside this skill, in
      `OPEN-WORK.md` rank 45, and the skill follows the tool rather than deciding
      for it.

## Quality

- [x] Present tense, no session narrative, no provenance.
- [x] No address, MAC, hostname or path added.
- [x] Frontmatter untouched, so no routing keyword moved.
- [x] The skill is now internally consistent: every reference to the PCIe floor
      quotes one spelling.
