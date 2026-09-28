# Checklist - every quoted block reproduces, and the skill names `--output` and `-h`

## The change this documents

Three output blocks in the skill did not reproduce against the committed captures, and the
skill named snapshot's `-o` and `--help` only by one spelling each. A `snapshot -o <file>` whose
standard output refuses the confirmation line now says the capture landed.

## Change

- The topology excerpt is the real `topology` output over `linux-sas-hba` at width 120: box
  drawing, the root-port name the PCI database gives, and the `>` clip marker.
- The kernel-virtual tally is the one `linux-minimal` prints: `3 not listed: 1 loop, 1 zd, 1 zram`.
- The trend rows are in the order the table prints them (by device), and the sentence after
  them names `sdd` and `sdj` instead of "the two top rows".
- `--help` is followed by "(or `-h`)"; the snapshot paragraph says `-o` is `--output` in full.
- The `74` row says a `snapshot -o <file>` whose standard output refuses the confirmation line
  leaves `74` although the capture landed, and quotes the line that says so.
- `logdemo --theme` stays out: `logdemo` is a developer helper the skill names only in prose.
- No frontmatter change.

## RED

- [x] Text check of the artifact: the three blocks were strict xfails in
      `tests/test_quoted_output_is_reproduced.py` - 5 of the topology excerpt's 8 lines matched
      nothing at any width from 80 to 140, the tally matched no capture, and the trend rows were
      out of order.
- [x] `redcheck --corpus-cascade` reported inherited coverage (STRONG) for the `-h` question: a
      memory fact about a click guard that could not see `-h`. So the behavioural arm is an
      excerpt-only probe told to use no outside knowledge, and the text checks below are the
      primary evidence.
- [x] Retrieval probe (text-only agent, current passages only), four questions. NONE for the
      long form of `-o`, NONE for the short form of `--help`, NONE for whether a capture landed
      when the run left `74`; NONE for `logdemo`'s options, which is the intended answer.

## GREEN

- [x] The three xfails are removed and all three blocks reproduce; the skill-reading,
      documented-surface, exit-code and translation tests pass (139).
- [x] Same four questions on the new passages. Direct quotes: "With `-o` (`--output` in full)",
      "`lsdsk <command> --help` (or `-h`)", and "the run leaves `74` although the capture
      landed"; `logdemo` still NONE.
- [x] The quoted error line was run through the real CLI: `snapshot -o <file> > /dev/full`
      prints `Error: wrote the capture to <file>, but standard output refused the line saying so`.

## REFACTOR

- [x] Gap closed: the `74` row said the capture landed and then "the output did not arrive".
      Now "some output did not arrive".
- [x] Gap closed: the GREEN probe had to infer that a line naming the file also confirms the
      write. The row now quotes the line, which begins "wrote the capture to".
- [x] Gap declined: `logdemo`'s options. It is a developer helper, and the skill tells a reader
      never to reach for it; `--help` lists them.

## Deployment

- [x] Every quoted block comes from a committed capture; no address or path from a real machine.
- [x] `COMMANDS.md` and `de/COMMANDS.md` carry the same `74` sentence, and the translation
      manifest is refreshed.
