# Skill review - 2026-09-18

Three corrections to `skills/lsdsk/SKILL.md`: a false exit-code claim, the exceptions the Python
example can raise, and the truncation of the printed `wwn` column.

## RED

Scenario: an inert probe with no tools, given only the affected excerpts and three retrieval
questions a reader would actually act on.

- [x] Q1, exit code when a diagnostic run's hardware read is refused. The probe answered that exit
      13 "is explicitly ruled out here" and quoted the line it relied on: "A diagnostic run never
      exits 13: it degrades to `-`". A wrapper written from that answer never handles 13.
- [x] Q2, which exceptions to catch around `snapshot.load` / `collect`. "None can be named from this
      documentation... guessing `FileNotFoundError`, `PermissionError`, `ValueError`, or some
      `lsdsk`-specific error type would be invention."
- [x] Q3, whether a `wwn` copied from piped human output can be incomplete. "No - nothing in the
      text suggests a truncation risk."
- [x] Contamination checked. The probe volunteered that the repo's own CLAUDE.md, which it inherits,
      contradicts its Q3 answer, and said it had excluded it as instructed. So the Q3 arm is
      inherited-contaminated in the cascade and honest only because the probe declared the conflict;
      Q1 and Q2 are not taught anywhere in that cascade.

## The claim under test, against the code

- [x] Exit 13 reproduced: a `PermissionError` raised at the hardware-read seam exits 13 from
      `findings`, `topology` and the bare page alike. `load_inventory` converts it, and every
      diagnostic command goes through `load_inventory`.
- [x] Pinned by a test that did not exist:
      `test_a_diagnostic_run_exits_13_when_the_hardware_read_is_refused`, parametrized over the
      three entry points.
- [x] `--full-wwn` confirmed present in `lsdsk disks --help` before being documented.

## GREEN

Same probe, same three questions, corrected text.

- [x] Q1: "Exit code `13`", quoting the rewritten row.
- [x] Q2: `ConfigurationError` and `PermissionError`, and it correctly derived that
      `UnsupportedPlatformError` needs no clause of its own because the text says it is a subclass.
- [x] Q3: "Yes", with `--format json` or `lsdsk disks --full-wwn` named as what to do instead.
- [x] `Skill gaps` requested in both arms and recorded. GREEN reported four, all scope limits of the
      excerpt rather than of the skill: which subcommands count as "a diagnostic run", what the
      exit-1 row's "see below" points to, which exit code a degraded-but-complete run gets, and the
      literal character that marks a cut value. Declined: the first three are answered by sections
      outside the excerpt, and the marker character belongs in the rendering documentation, not in
      the advice about quoting a drive.

## Diff both directions

- [x] Nothing the RED arm produced is missing from GREEN. RED's only correct output was its refusal
      to invent exception names in Q2, which GREEN replaces with the real ones.

## Quality

- [x] Frontmatter untouched; no `name` or `description` change, so no routing keyword moved.
- [x] No session narrative, no scratch paths, no machine-specific values in the edited text.
- [x] Every added claim checked against the running tool, not against the source alone.
