# Checklist - the exit-code table, the callables, and the two value sets

Four omissions in one file, closed in one pass because they are one file and the
skill-writer is the only way it changes.

- The exit-code table stopped at `78`. `141` has been reachable since the
  broken-pipe work and is what every `lsdsk ... | head` leaves, so a reader
  consulting the table to branch met an undocumented code the first time they
  piped anything. Nothing said which code wins when the reader also left, and
  nothing said that the answer does not move with `--format`.
- `2` was "the CLI framework's usage error" and nothing more. It is the named
  member `USAGE_ERROR` now, and a refused command line prints the failure
  envelope on stdout when the command line asked for JSON.
- The `22` row named no flag, and the only flag that produces it, `config
  --section`, had zero occurrences in the file - while `--set SECTION.KEY=VALUE`
  is documented throughout using the same word SECTION.
- The `13` row named `config-deploy` alone, though `snapshot` and `record` both
  leave it for a destination that refuses to be written.
- The Python surface named `load`, `collect` and `diagnose`. `snapshot` exports
  seven more names and `diagnostics` eleven more, including
  `count_by_severity`, which the skill's own shell example works around.
- The `[thresholds]` enumeration named six of seven keys, missing
  `wear_projection_min_points`.
- `controller.kind` was never named, though `disk.bus` and `disk.kind` have
  their value sets spelled out.

## Change

- The table gains a `141` row, and the `1`, `13` and `22` rows gain the causes
  they were missing. Two paragraphs follow it: the usage-error envelope with its
  `error.type`-is-the-code's-name rule, and the ranking, stated as
  format-independent because that is the property a wrapper needs.
- The ranking carries its own exception rather than reading as absolute: a
  command already writing when the reader leaves exits `141` before deciding
  anything, which is why `config --section nosuch` into a departed reader leaves
  `141` and not `22`.
- Two paragraphs name the exported callables, each with its signature and what it
  is for, rather than a bare list.
- One paragraph for `data.controllers`, giving `kind`'s seven values and the
  fields beside it.
- `wear_projection_min_points` joins the thresholds sentence with the reason its
  floor exists, and the sentence now says "all seven".
- The `config` line in the command list names `--section`.
- No finding text, no link figure, no threshold VALUE and no frontmatter moved.

## RED, GREEN

Two instruments, because the arms are contaminated differently.
`redcheck --corpus-cascade` reports STRONG inherited coverage for the callables
and the 141 scenarios - this repo's own `CLAUDE.md` is in the cascade an agent
dispatched here inherits - and clean for the exit-22 arm. So the completeness
claims are evidenced by a text check of the artifact, which inherited context
cannot reach, and the one clean arm was also driven behaviourally.

- [x] RED (text): all 14 terms the change is about occur ZERO times in the
      pre-change file: `141`, `USAGE_ERROR`, `BROKEN_PIPE`, `--section`,
      `count_by_severity`, `wear_projection_min_points`, `ControllerKind`,
      `is_storage_controller`, `parse_capture`, `read_current_machine`,
      `build_from`, `current_platform`, `attached_demand_gbytes`,
      `format_pcie_sentence`.
- [x] RED (behavioural, the arm redcheck found clean): a probe given the
      pre-change text and asked to reproduce exit 22 answered
      `lsdsk --set bogus_section.some_key=1 findings`, reasoning that `--set` "is
      the one option in the reference that names a config *section* explicitly".
      That command line exits `0` in silence - measured - so the reader would
      have concluded the code is unreachable. The same probe called `141` "not
      one of the codes this tool defines" and listed six thresholds keys.
- [x] GREEN: the same four questions against the new text give
      `lsdsk config --section nosuch` with `--set` explicitly rejected; `141` as
      neither a verdict nor a refusal, format-independent, with the
      already-writing exception carried across unprompted; all seven keys; and
      `record`'s `13` read correctly as a write refused rather than a verdict.
- [x] Every claim written was measured against the built tool first, not read off
      the source: `config --section nosuch` leaves 22, `disks --bogus --format
      json` prints the quoted object and leaves 2, a refusal into a departed
      reader leaves 78 and 2 in BOTH formats while a verdict leaves 141 in both,
      `config --section nosuch` into a departed reader leaves 141, `--set
      bogus.x=1` leaves 0 in silence, `record` writes 0 bytes to both streams,
      and `record --format json` on an unwritable store exits 13 with the
      refusal in `skipped`.
- [x] The quoted JSON object is the tool's own output, pasted rather than
      composed.
- [x] `test_skill_quotes_output_the_tool_produces`,
      `test_skill_describes_the_fabric_view` and `test_docs_show_the_trend_table`
      pass, so no link figure, disk-field enumeration or trend row moved.

## Skill gaps

The GREEN probe reported five; two were real and are closed, three declined.

- [x] CLOSED: it could not tell what a `--format json` run leaves on stdout when
      141 lands mid-write. A paragraph now says not to parse what arrived,
      because whether anything did is a question about buffering rather than
      about the command.
- [x] CLOSED: it could not tell whether `record` in JSON mode answers a refused
      write with an error envelope the way a usage error does. It does not - it
      prints the action envelope with the refusal in `skipped` - and that is now
      stated, with the instruction to read `ok` then `skipped` rather than
      `error.type`.
- [x] DECLINED: "the reference never states what verdict the run would have
      reached had the reader stayed". Nothing can state it; that is what 141
      means.
- [x] DECLINED: remediation steps for a timer meeting `13`. The table's job is
      what a code MEANS; the skill already says where the store lives, which is
      the half that is a fact rather than a runbook.
- [x] DECLINED: the `or` between the causes listed for one code. Several causes
      per code is the truth, and the message on stderr is what separates them -
      which the `2` paragraph already says.

## Left for another pass

- `skills/lsdsk/SKILL.md` still describes `--set` as the way to move a judgement
  without saying that a MISSPELLED section name is accepted in silence. That is a
  defect in the tool rather than in the skill, tracked on its own.
