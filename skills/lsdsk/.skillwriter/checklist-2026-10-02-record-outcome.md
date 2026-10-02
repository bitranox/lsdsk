# Checklist - record's nothing-new envelope and `data.outcome`

## The change this documents

`lsdsk record --format json` with nothing new to store (no drive's power-on hours
advanced) used to report `ok` false with the reason in `skipped`, the same shape as
a failed write or an unreadable store. It now reports `ok` true, `recorded` false
and an empty `skipped`, and every run's `data` carries `outcome`: `recorded`,
`nothing new`, `store not readable`, `not permitted` or `could not write`.

## Change

- The "A failed write is reported as a SKIP" paragraph names `recorded` false for
  the failures, the `74` arm beside `13`, the healthy nothing-new envelope, the
  five `outcome` values, and that `ok` false means the record has stopped growing.
- The `record` field list in the read-only-pipeline paragraph gains `outcome`.
- No frontmatter, no exit code moved.

## RED

- [x] Retrieval probe (text-only agent, the passages as they stood), scenario: an
      hourly timer running `record --format json` with nothing new. Q1 (nothing-new
      `ok`, `skipped`) and Q3 (tell nothing new from an unreadable store from a
      failed write in JSON) answered NONE; Q2 alarmed on
      `.ok == false and .data.recorded == false`. Its gaps named the undescribed
      nothing-new envelope.
- [x] Contamination: the probe saw the code commit's subject line in its injected
      git context and set it aside, as instructed; its NONE answers are from the
      excerpt.

## GREEN

- [x] Same probe on the new passages: Q1 quoted the nothing-new sentence (`ok`
      true, `skipped` empty, exit `0`); Q2 alarmed on `.ok == false` alone,
      quoting "false means the record has stopped growing", and explained why
      keying on `recorded` would fire every quiet hour; Q3 named `data.outcome`.
- [x] Diffed against RED both ways: no RED result is missing from GREEN.

## REFACTOR

- [x] Gaps declined: `recorded` for the failures is stated ("`ok` false,
      `recorded` false", and the `78` case "answers the same way"); cross-checking
      the exit code against `outcome` is the exit-code table's job; the `error`
      object's shape is documented in the paragraph above this one in the full
      skill.

## Deployment

- [x] Every test that reads SKILL.md passes (8 files, 144 tests).
- [x] No address, host or path from a real machine added.
