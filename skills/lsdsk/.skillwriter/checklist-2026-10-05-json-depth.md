# Checklist - the 95-level --set limit and config's JSON depth refusal

## The changes this documents

1. A `--set` value nested deeper than 95 levels is refused at exit `2` (it was 100).
2. `config --format json` leaves `78` for a configuration nested deeper than the platform's JSON
   writer carries - 98 levels on Windows - and names `--format human`, which prints it.

## Change

- The exit-2 paragraph's nesting figure reads 95.
- The `78` row of the exit-code table gains the depth case with its Windows figure and the
  `--format human` pointer.
- No frontmatter changed. The figures come from the code (`MAX_OVERRIDE_DEPTH`) and from a
  bisection of pydantic-core 2.46.5's JSON writer on Windows and Linux; the refusal sentence and
  exit code were observed by running `config --format json` on Windows over a 98-level file.

## RED

- [x] Contamination check first: `redcheck --corpus-cascade .` over a retrieval scenario for both
      facts reported STRONG inherited coverage - a memory fact on pydantic-core's Windows depth
      ceiling and the cascade share its terms - so a behavioural probe could answer from inherited
      context. The behavioural arm is replaced by a text check of the artifact.
- [x] Text check on the passages as they stood: four assertions (the limit reads 95, no 100-level
      limit remains, the `78` row names the depth refusal of `config --format json`, the row names
      `--format human`) - all four FAIL.

## GREEN

- [x] Same text check on the edited file: all four PASS.
- [x] Every test file that reads `SKILL.md` (8 files, found by filename) passes.

## REFACTOR

- [x] Nothing else in the skill states the limit or the `78` cases; `grep -n '100 levels'` over
      the skill returns no hit, and the other `78` mentions describe `record`, `--replay` and the
      error envelope's type name, none of which changed.
