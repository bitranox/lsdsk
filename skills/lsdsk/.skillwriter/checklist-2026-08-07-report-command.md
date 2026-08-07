# skill-writer review: the page becomes a command, not a flag

Every section of the page is a subcommand. The page itself was a global flag,
which is inconsistent on its face and produced a rule nothing else in the CLI
needs: a flag that means something in one position and is refused in another.

The reason recorded for keeping it out of the command list - that a command
would be "a redundant entry in `--help` beside the sections it already contains"
- was written when a bare `lsdsk` WAS the page. Since the interactive view took
that position, the page has no name at a terminal, so the entry is not
redundant. The premise changed; the decision had not.

## What changed in the skill

`--report` becomes `lsdsk report` throughout, the position rule disappears with
the flag it governed, and the command list gains a row separating what a bare
run does from what the page is.

## Verification

This edit is a rename following a settled interface change, so the risk it
carries is not that a reader is persuaded of the wrong thing - it is that the
text shows a command line that no longer parses. That is executable, so it was
executed rather than reviewed:

- [x] Every invocation the skill SHOWS was extracted and run against the built
      program under a per-call timeout: 18 lines, 0 real failures. Three were
      flagged and each read back: two are prose naming a form rather than a
      complete line (`lsdsk record --replay`), and one is the skill's own
      documented negative example, `lsdsk record --history-file h.json`, which
      it states "is the usage error that exits `2`". The check reported exactly
      what the text predicts.
- [x] The same run over the README: 13 lines, 0 real failures.
- [x] Neither document mentions `--report` any more, and both name
      `lsdsk report`.
- [x] Every registered command except the two test vehicles appears in both
      documents.
- [x] `lsdsk report --replay <capture>` renders the page and exits from the
      findings; `lsdsk --report` now exits 2 with "No such option".
- [x] The first probe of this hung, because it launched `lsdsk tui` and waited
      for a keypress - the failure mode the command exists to prevent. Bounded
      and re-run.

No subagent scenario was run for this edit. The RED/GREEN pair for the
behaviour itself is `checklist-2026-08-07-report-flag.md`, and nothing about
what the text TEACHES changed here, only the spelling of the invocation.
