# Checklist - `record` leaves `78` for a store it cannot read

## The change this documents

`lsdsk record` exits `78` (EX_CONFIG) when the counter-history store it would add
to cannot be read - another machine's, written by a newer lsdsk, too large, or
not valid JSON. It keeps the store untouched and records nothing, as before; it
used to leave `0`, identical to a run with nothing new to store. In JSON mode the
envelope carries `ok` false, `recorded` false and the reason in `skipped`.
Reporting commands meeting the same store are unchanged: they warn and exit on
their findings.

## Change

- The paragraph above the exit-code table says `record` also leaves `78` for an
  unreadable store, and why.
- The `78` row names "a counter-history store `record` cannot read, which it
  keeps untouched and adds nothing to".
- The failed-write SKIP paragraph says an unreadable store answers the same way
  at `78`.
- The "cannot READ the history" paragraph scopes "the exit code still reflects the
  findings" to reporting commands and states `record`'s three stderr lines and
  its `78`.
- No frontmatter change.

## RED

- [x] Text check of the artifact: `test_each_exit_code_table_names_every_cause_of_the_code`
      gained `counter-history store` as a `78` cause. It was RED on SKILL.md
      before the edit. The module reference row already carried the phrase from
      the same change; its committed text at HEAD did not.
- [x] `redcheck --corpus-cascade` reported inherited coverage on generic terms
      only (backup, hostname, systemd, timer, non-zero). None of the three named
      documents mentions `lsdsk record`, `EX_CONFIG` or `CONFIG_ERROR`, so the
      lesson is not inherited; the text check stays the primary evidence.
- [x] Retrieval probe (text-only agent, the pre-change passages only), three
      questions about a `record` run over another machine's store: the exit
      code, whether the file is overwritten, and the JSON `ok`. All three
      answered NONE; the probe noted the table covers only WRITE failures of the
      history file.

## GREEN

- [x] Same three questions plus a fourth (`health` over the same store with
      healthy drives) on the new passages. All four answered by direct quote:
      `78` from the paragraph above the table; "the file is INTACT"; "`ok`
      false, `recorded` false, the reason in `skipped`"; `0` from "On a
      reporting command the hardware is still diagnosed".
- [x] Every stated behaviour run through the real CLI over a truncated store:
      human mode left `78`, nothing on stdout, and three stderr lines (the two
      warnings, then the `Error:` line); `--format json` left `78` with `ok`
      false, `recorded` false and the sentence in `skipped`; the store was
      byte-identical afterwards.

## REFACTOR

- [x] Gap closed: the probe could not tell whether `record` prints the two
      warning lines before its own `Error:` line. The paragraph now says "it
      prints those same two lines, then a third". Verified by text.
- [x] Gap declined: the probe had to infer that `health` is one of "the eight
      section commands", because its excerpt omitted the list. The full skill
      names them in the read-only pipeline paragraph; restating the list here
      would repeat it.

## Deployment

- [x] Every path in the change is a placeholder (`<path>`, `<why>`); no address
      or path from a real machine.
- [x] The skill-reading tests pass: the exit-code tables and causes, the
      quoted-output check and the documented-surface guard.
