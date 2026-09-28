# Checklist - `snapshot -o -` writes to stdout, and 22 gains the cause that refuses it with JSON

## The change this documents

`lsdsk snapshot -o -` now writes the capture to standard output, as a dash means
for every Unix tool, and refuses `-o -` together with `--format json` at `22`,
because the capture and the result envelope would both be stdout. Only the bare
dash means stdout; `-o ./-` still writes a file named `-`.

## Change

- The `22` row of the exit-code table names the new cause.
- The "`snapshot` is the exception" paragraph says `-o -` writes the reading to
  stdout, and that this is why it refuses `--format json`.
- The pipeline paragraph that lists `snapshot`'s `--replay` refusal names the
  second refusal beside it.
- The snapshot section under the history heading gains the remote one-liner and
  the `./-` escape, with `host.example` as the host.
- No frontmatter, no finding text, no other exit code moved.

## RED

- [x] Text check of the artifact: `test_each_exit_code_table_names_every_cause_of_the_code`
      pins "`-o -`" in the `22` row of both tables. RED on the SKILL.md and the
      module-reference arms before the edit; GREEN after.
- [x] redcheck over this directory's cascade reported inherited coverage on four
      documents, matched on generic terms (dash, exited, separately, replay).
      Adjudicated against their text: none of the four contains `snapshot -o -` or
      "standard output", and no memory fact body contains `snapshot -o -`. So the
      behavioural arm below is not answered from the cascade; the text check above
      is still the primary evidence.
- [x] Retrieval probe (text-only agent, current passages only), three questions:
      a one-line remote capture that leaves no file on the server, the cause of a
      `22` from `snapshot -o - --format json`, and what `-o -` does. It answered
      NONE to all three, and its gaps named the missing `-o -` convention and the
      `22` table having no `snapshot --format` cause.

## GREEN

- [x] Same probe on the new passages, plus a fourth question (how to get a file
      really named `-`). All four answered with a direct quote of the governing
      text: `ssh db1.example.com lsdsk snapshot -o - > capture.json`; the `22`
      sentence beside the `--replay` refusal; the stdout paragraph; `-o ./-`.

## REFACTOR

- [x] Gap closed: the process-substitution form sat as a trailing comment on
      the `--replay capture.json` line, so it read as a second way to write
      `capture.json`. It is its own line now, commented as replaying without
      keeping a file.
- [x] Gap declined: substituting the question's host for `host.example` is the
      reader's job; a placeholder host is the documented convention.

## Deployment

- [x] Every example host is `host.example` (RFC 2606); no address or path from a
      real machine.
- [x] The one-liner was run for real: `snapshot -o - > capture.json` exited 0,
      left no `-` file, and `--replay` of the redirected file exited as the live
      run did; `--replay <(...)` reads a pipe.
