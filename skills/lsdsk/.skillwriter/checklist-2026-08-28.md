# Skill review - skills/lsdsk/SKILL.md, 2026-08-28

Reference skill. Two claims in it disagreed with the code it documents: the rendered
`lsdsk trend` example printed a column header the renderer no longer emits, and the
recording paragraph did not say what a recording run does to a drive whose own
power-on hour has not advanced.

## RED

The behavioural arm cannot fail honestly for either claim. `redcheck --corpus-cascade`
over this machine's always-loaded context reports STRONG inherited coverage for both
scenarios, naming the documents that already teach them, so a dispatched agent answers
from its own context rather than from the skill. The behavioural arm is therefore
replaced by a text check of the artifact against the code, which no inherited context
can supply.

- [x] RED: a check that derives the trend headers from `TREND_COLUMNS` and the
      replacement rule from `_fold_in` fails on both counts against the pre-change
      text. Verbatim: `trend example headers [... 'change', 'over', 'per', 'hour',
      'verdict'] != printed [... 'change', 'span', 'per hour', 'verdict']` and
      `_fold_in replaces the newest row; SKILL.md never says so`.
- [x] Non-vacuous in both directions: with the corrected file in place, restoring
      the `over` header alone fails the first assertion and nothing else, and deleting
      the replacement sentence alone fails the second and nothing else.
- [x] Ground truth read from the source, never hard-coded in the check, so the check
      cannot outlive the interface it guards.

## GREEN

- [x] Both assertions pass against the corrected text.
- [x] A reader given only the two edited passages, and no access to the repository or
      the installed skill, answers both questions correctly and quotes the governing
      sentence for each: the header is `span`, and a drive whose clock stood still
      gains zero rows while its newest row is replaced.
- [x] That reader was required to return a `Skill gaps` section, and returned six items.

## REFACTOR

Gaps closed:

- [x] `one reading per run` beside `covers every drive` read as a contradiction unless
      the reader supplied an unstated `per drive`. The sentence now says the run
      records every drive it read, and the clause is gone.
- [x] The passage never equated a drive's `own clock` with its power-on hours. It now
      names them in the same breath.
- [x] The per-drive cap was stated without a way to act on it. The configuration key
      is now named. The number is not, because it is configurable and would go stale.

Gaps declined, with reasons:

- [x] `were due` undefined, the verdict wording unexplained, and why a rate prints `-`
      rather than `0`: all read from two excerpts rather than the whole skill, which
      defines the first two where the trend verdicts are discussed. Out of scope for a
      correction pass, and covered.

## Verification

- [x] Frontmatter unchanged; `name` and `description` untouched, so no routing keyword
      moved.
- [x] The corrected header now has a regression guard in the suite:
      `tests/test_docs_show_the_trend_table.py` derives the expected header from
      `TREND_COLUMNS` and checks both documents that print the table, the README
      included. The prose claim about replacement is left unguarded deliberately: a
      keyword match on wording is brittle enough to fail on a legitimate rewrite.
- [x] No address, hostname or path was added. The device paths in the example are the
      kernel's own generic names.
- [x] No session narrative, no scratch paths, no operator instructions in the skill or
      in this artifact.
- [x] `make test` green on the whole tree, including the new guard.
