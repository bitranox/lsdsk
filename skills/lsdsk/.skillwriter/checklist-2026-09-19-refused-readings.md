# Checklist - a refused reading is named, per device, with the reason

The envelope reports a reading the machine REFUSED, so `ok` is false and
`skipped` names the device that said no. The skill enumerated a disk's fields
without `readings_refused` and described `skipped` as privilege-and-hypervisor
sentences only, which is the shape that misleads a caller rather than merely
omitting: an enumerated list reads as the whole payload, so a field nobody knows
to read is a field nobody reads.

## Change

- The disk-field sentence gains `readings_refused`, says it is a list that is
  empty on a drive that answered, and names its two keys.
- The `skipped` paragraph gains the per-device form `<reading>: <subject> -
  <reason>`, the two refusals that occur as root, and the fact that the entry
  names its subject.
- No rule, threshold, exit code or finding text moved. Frontmatter untouched.

## RED, GREEN

The lesson under test is a FACT ABOUT THIS PAYLOAD, so the evidence is a text
check of the artifact against the real envelope rather than a dispatched
baseline: an agent's answer about which fields a disk carries would come from
this repo's own cascade, not from the scenario.

- [x] RED: `test_the_skill_enumerates_the_fields_a_disk_really_carries` fails on
      the pre-change skill, naming the omission - `only in the payload
      ['readings_refused']`.
- [x] RED is not vacuous: the same test asserts the enumerating sentence was
      found at all, so a moved or reworded sentence fails as an unchecked claim
      rather than passing silently.
- [x] The expected set is taken from a REAL envelope built from a committed
      capture and dumped in JSON mode, never typed into the test, so a field
      added to the payload fails until the skill follows.
- [x] GREEN: both skill test modules pass, and the full suite is green.

## Skill gaps

- [x] The `skipped` sentence could have been left as it was, since "sentences
      saying what was not done and why" is not false of a refusal line. Closed
      anyway: the sentence a caller acts on is the one that says whether a
      privileged run can be incomplete, and it read as though it could not.
- [x] The per-device form is quoted as a shape rather than as a literal line.
      A literal would carry a device path and an OS message from one machine,
      which the field-list guard cannot check and a reader would grep for.
- [x] No exit code changes, so the exit-code table is untouched. Whether a
      refusal should raise its own finding is a product question for the
      diagnostics rules, not a documentation gap; left open deliberately.
- [x] None undecided.

## Quality

- [x] Present tense, no session narrative, no provenance.
- [x] No address, MAC, hostname or path added.
- [x] Frontmatter untouched, so no routing keyword moved.
