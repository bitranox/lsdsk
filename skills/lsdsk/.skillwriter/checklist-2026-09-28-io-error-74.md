# Checklist - a failed write leaves `74`, and the `-o -` recipe says how to keep a capture clean

## The change this documents

A write this tool was asked to make that fails for a reason other than permission
leaves `74` (EX_IOERR) instead of `1`, because on a reporting command `1` is the
verdict that a warning or a critical was found. That covers a `snapshot`
destination, the counter-history file `record` writes, the files `config-deploy`
and `config-generate-examples` write, and standard output for a command whose
output it is - refused (a full disk) or absent (`>&-`, pythonw). `74` stands
when the reader has also gone. `snapshot -o -` is also refused at `22` while the
logging console writes to stdout, and `snapshot.serialise()` is public.

## Change

- The exit-code table gains a `74` row; the `1` row no longer claims failed
  writes, and says a failed write is `74`; the `22` row names the logging-console
  cause; the `70` row says a failed write is `74`, not its errno.
- The paragraph above the table says `13` for permission and `74` otherwise, and
  that `record` prints nothing in human mode only when it succeeds.
- The `141` ranking paragraph says `74` stands, and why.
- The `lsdsk.adapters.hw.snapshot` paragraph names `serialise(capture)` and which
  pair each `-o` form is made of.
- The `-o -` section gains three paragraphs: keep stderr off the redirect
  (`2>&1`, `&>`, `ssh -t`), a redirected capture takes the shell's umask
  (`umask 077`), and a PowerShell 5.1 capture (UTF-16 or UTF-8 with a BOM) loads.
- No frontmatter change.

## RED

- [x] Text check of the artifact: `test_each_exit_code_table_names_every_cause_of_the_code`
      requires the `74` row to name `snapshot`, `record`, `config-deploy`,
      `config-generate-examples` and `standard output`. All five arms were RED on
      the SKILL.md before the edit, and `test_every_code_the_tool_can_raise_is_documented_where_a_caller_reads`
      was RED for the missing code.
- [x] `redcheck --corpus-cascade` reported inherited coverage on generic terms
      (byte, mark, redirect, page, storage). Adjudicated by grepping the named
      documents for `EX_IOERR`, `IO_ERROR`, `74`, `umask 077` and `serialise(`:
      the one hit is an unrelated credential-copy recipe using `umask 077`, and no
      document or memory fact names this tool's `74` or the `-o -` stderr rule.
      The text check above is the primary evidence.
- [x] Retrieval probe (text-only agent, current passages only), six questions.
      It answered NONE for the code a full disk leaves on `findings > file`, for
      the in-memory text function, for making the capture owner-only and for a
      PowerShell capture; it answered `1` for a `record` on a full disk (what the
      old text said); and it could explain the `ssh -t` failure only by its own
      inference, "not a sentence the excerpt states outright".

## GREEN

- [x] Same six questions plus a seventh (`74` against a departed stderr reader)
      on the new passages. All seven answered by direct quote: `74` and "Never a
      verdict about the machine"; `74` for `record`; `serialise(capture)`; the
      "Keep stderr off the redirect" paragraph; `umask 077` or `-o`; "A capture
      saved on Windows loads"; "`74` stands as well".
- [x] Every behaviour the text states was run through the real CLI:
      `findings > /dev/full`, `--version > /dev/full`, `snapshot -o - >&-` and a
      `record` whose history path cannot exist each left `74` with one stderr
      line; `snapshot -o <file> >&-` left `0` with the file written 0600.

## REFACTOR

- [x] Gap closed: the probe had to equate "history store" with "a `record`
      destination". The row now says "the counter-history file `record` writes".
      Verified by text: the row contains that phrase.
- [x] Gap closed: running the documented cases exposed that `findings >&-` still
      crashed with `70`, which made the new "or closed" claim false. The code was
      fixed, and the row now scopes the claim to a command whose output IS
      standard output, adding that `snapshot -o <file>` needs none.
- [x] Gap declined: the seventh answer combines two sentences (the `74` row and
      the ranking paragraph). One sentence per combination of conditions would
      repeat the ranking for every code; the ranking paragraph is where it lives.

## Deployment

- [x] Every example host is `host.example`, every path generic; no address or
      path from a real machine.
- [x] The skill-reading tests pass: the exit-code tables, the quoted-output
      check, the documented-surface guard and the package-agreement check.
