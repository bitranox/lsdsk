# skill-writer review: what the author of a document cannot see in it

## Why this pass exists

The previous pass was written and verified by the same agent. This one was run by an
agent given the file and nothing else: no knowledge of what had changed, why, or by
whom. It found five HIGH errors in text that had just been reviewed and called clean,
which is the argument for the arrangement rather than an incident.

## What changed

Corrections to claims the code contradicts:

- The history store path named only the per-user directory. A root run uses
  `/var/lib/lsdsk/history.json`, and reading the counters needs root, so the document
  named the path of the run that never records and omitted the path of the run that
  does - while telling an operator to move that file aside.
- `uvx lsdsk config-deploy` as printed is a usage error: `--target` is required, and it
  is the one enum a caller must write that the document never mentioned.
- `trend --format json` returns the standard machine-wide envelope. The verdict, the
  rate and the `were due` figure - the three things the longest chapter tells you to act
  on - exist in the human table only.
- Exit `1` was described as "a warning or critical". It is also what an internal error
  leaves, and `record`, `snapshot` and the `config-*` commands exit `0` whatever the
  hardware says, so a wrapper alerting on their code alerts on nothing.
- Exit `13` cannot come from a diagnostic run at all; it comes from `config-deploy`
  without root. Sitting in the same table it invited a "re-run with sudo" branch that
  never fires while the real unprivileged run returns 0 or 1 and is blind.
- The default page lists ten sections, not seven; the controllers table, the SMART
  attributes and the trend section were missing, the last being the one the document
  elsewhere tells you to run separately.
- Four finding families had no row and no remedy: link never trained, the drive's own
  overall self-assessment reporting FAILED, temperature against the drive's declared
  limits, and controller oversubscription. Temperature is a trigger in this skill's own
  frontmatter.
- Slot numbers and connector detection were described as one privileged capability. They
  are independent: `FREE` is gated on connector detection alone, and two of three
  privileged captures carry no slot number at all.
- Yellow was undocumented on both the `link` and `port` columns, and yellow-link is the
  first finding on this project's own fixture - the colour an operator actually meets.
- The figures the rules turn on were withheld and presented as fixed. Six are listed now,
  with the `--set` form, all verified against `domain/thresholds.py`.
- `counter reset`, the fifth trend verdict, had no reading. It is the one that means the
  history is suspect rather than the drive.
- `lsdsk info` does not print install paths; the environment values are four, not three;
  the global option list omitted `--set`, `--version` and `--env-file`.

Corrections to text the PREVIOUS pass introduced:

- "No row here carries a fixed severity" was wrong in the strong direction. Only
  `_sector_findings` and `_crc_findings` call `refine()`; everything else is decided by
  rule, wear included. The replacement first said "two rows" and then listed five
  counters, conflating two functions with five table rows; it says five.
- A new global-option list said "all before the subcommand", contradicting the correct
  statement four lines above that `--replay` and `--profile` work in either position.
- A new sentence claimed every run reads the root store and that only writing needs root.
  The two paths are separate files: an unprivileged run neither reads nor is refused by
  the root one.
- The new exit-`22` cause (`snapshot` given `--replay`) was in the prose and missing from
  the table that enumerates the causes.

Code changed, because the skill would otherwise have documented a defect as behaviour:

- `snapshot` silently ignored the global `--replay` and captured the local machine into a
  file the caller believed held another host's reading, at exit 0. It refuses with exit
  `22` now. The structural test written to prevent exactly this class of bug did not
  catch it, because it skipped any command not declaring a `replay` parameter; it now
  checks every command that reads a machine.
- Two `[display]` temperature keys were parsed and read by nothing, under a comment
  promising they changed severity and the exit code. Removed.

## RED

Baseline, one dispatch, three operator situations, against the pre-change text.

- [x] "Does the refused run still store its reading?" -> "Yes, almost certainly."
- [x] "Restore from backup?" -> correct conclusion reached with no support in the text.
- [x] Read-only JSON pipeline -> listed `record` among the safe commands.
- [x] Exhaustive exit-1 conditions -> twelve, the maker's-threshold attribute absent.

## GREEN

Ten questions, same file, a verbatim quote required per answer.

- [x] All ten answered by direct quote. No NONE, no paraphrase.
- [x] Three gaps reported and all three closed: the exit-`22` table row, where `record`'s
      JSON fields sit, and the concrete per-user path rather than the variable name.

## REFACTOR

- [x] A second reader, asked only for statements that cannot both be true, found four.
      All four closed: the row-count, the exit-`1` table row against its own prose, "each
      phy is a real port" against the 9500-16i publishing 21 on a 16-lane card, and an
      ambiguous antecedent in the store-path paragraph.
- [x] A third reader, same question, found three more. All three closed: the
      option-position contradiction, the worked example fetching the one vendor the
      document says answers 403, and the store-permission claim.
- [x] Each fix verified by quote-back before the next pass.
- [x] Every figure written in was checked against the code: six thresholds, four
      environment values, eight global options, the config filename, the four added
      finding families, the `record` envelope shape.

## Checks

- [x] Frontmatter unchanged; `name` and `description` only, triggering conditions only.
- [x] Reference/hub skill, over the 500-word target by design; no supporting files added.
- [x] No session narrative, no operator instructions, no scratch paths.
- [x] The worked example no longer names a real vendor host, and none was added.
- [x] `make test` green after every pass.
