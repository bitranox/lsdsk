# skill-writer review: three behaviours the document described as they no longer are

## What changed

- A run that cannot READ the counter history now refuses to WRITE it. The document said
  nothing about this at all, and the warning text it emits appeared nowhere. Both stderr
  lines are quoted now, with the four causes, and with the two facts an operator paged at
  night needs: the run is not a failure, and the file is intact so there is nothing to
  restore.
- "so a pipeline never mutates state" was true only of the REPORTING commands. `lsdsk
  record` writes under `--replay` and under `--format json`, because storing a reading is
  its purpose, and it emits the usual envelope in JSON rather than being the "no-output
  form" the document called it without qualification. The rule is restated with the safe
  and excluded commands both named.
- A SMART attribute below the drive maker's own published threshold is a critical finding,
  and was absent from the table of what a finding can be.
- Added in REFACTOR: no row of that table carries a fixed severity, and several move
  (CRC by recorded history, wear by its threshold, a mainboard ceiling by demand), so the
  document now says to read the marker rather than infer severity from the kind of finding.
- Added in REFACTOR: `info` and plain `config` are named as safe for a read-only pipeline,
  where before they were safe only by absence from the exclusion list.

## RED

Baseline against the pre-change text, three operator situations, one dispatch.

- [x] "Does the run still store its reading?" -> "Yes, almost certainly. The skill states
      the write rule unconditionally except for two named exceptions." The write happens
      only if the store can be read, and it no longer happens at all here.
- [x] "Is the history file intact or should they restore from backup?" -> "The skill does
      not say, and I would not jump to restoring from backup on this evidence alone",
      reached by reasoning from `--no-record` as an adjacent case. Correct conclusion,
      no support in the document.
- [x] Read-only JSON pipeline -> listed `record` among "Safe with `--format json`", on the
      quoted blanket promise. That invocation writes.
- [x] Exhaustive exit-1 conditions -> twelve, none of them the maker's-threshold attribute.
- [x] Gaps reported: the warning text is undocumented; whether `--format json` suppresses
      `config-deploy` is unsettled; the finding table has no severity column so four rows
      were graded by how imperative their remedy sounded.

## GREEN

Same three situations, same model, against the edited text, with a quote required per answer.

- [x] Storage -> "No", quoting "A run that cannot READ the history will not WRITE it
      either, and says so."
- [x] Restore -> "Yes, nothing to restore", quoting the INTACT sentence, and naming a
      renamed host as the likely cause from the document's own line.
- [x] Pipeline -> `record` excluded, quoting the restated rule; `snapshot` and both
      `config-*` excluded with it.
- [x] Exit-1 conditions -> thirteen, including "An attribute under the maker's threshold",
      and "Capped by the mainboard" correctly identified as the document's worked hint.
- [x] Every answer was a verbatim quote, not a paraphrase.

## GREEN diffed against RED, both directions

- [x] Gained: the two history answers, the `record` exclusion, the new finding kind.
- [x] Lost: nothing. Every condition RED enumerated appears in GREEN, which adds one and
      reclassifies one. RED's nuance that `snapshot` writes only where `-o` points is
      absent from GREEN, which excludes it outright; that is the safer answer for a
      pipeline forbidden to write to the host, so it is not treated as a loss.

## REFACTOR

- [x] Gap "the table has no severity column, so I inferred four rows from their wording":
      CLOSED, by stating that no row carries a fixed severity and directing the reader to
      the marker.
- [x] Gap "held back by its controller reads as actionable in the table and as a placement
      question in the prose": CLOSED by the same passage.
- [x] Gap "whether a mainboard ceiling can exceed hint is unstated": CLOSED by the same
      passage, which names demand as what moves it.
- [x] Gap "`info` and plain `config` are safe only by elimination": CLOSED by naming them.
- [x] Re-tested by quote-back on the five questions the fix touched. All five answered with
      a verbatim quote, none NONE, and no gaps reported.
- [x] Declined, by design: a yes/no severity for one named table row. The document answers
      "how do I find out" because the severity is not a property of the row.

## Checks

- [x] Frontmatter unchanged; `name` and `description` only.
- [x] Reference/hub skill, so over the 500-word body target by design; no supporting files
      were added.
- [x] No session narrative, no operator instructions, no scratch paths in the skill or here.
- [x] No address, MAC, hostname or path added that is not already in the document.
- [x] Every claim added is one the code now does, verified by running the tool.
