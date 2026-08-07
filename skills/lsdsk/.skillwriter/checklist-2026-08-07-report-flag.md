# skill-writer review: `--report`, and why a subprocess is not proof of a printed page

The skill told a reader that a subprocess always gets the printed page. That is
false for any caller which allocates a pseudo-terminal, and the false half is
the one that hangs.

## RED

Scenario: a colleague is adding lsdsk to an unattended CI notebook. Their cell is
`!lsdsk` followed by `assert _exit_code in (0, 1)`. Is it safe, or will it hang?

An isolated agent given the pre-change text answered:

> **No.** ... A Jupyter `!` shell-out is a subprocess, which the skill names
> directly in the "anywhere else" bucket, so it gets the printed page, not the
> interactive TUI waiting on a keypress. ... So: not a hang risk.

Confidently wrong, and sourced to the skill's own words. The same belief cost a
CI job 900 seconds before it was killed.

## GREEN

Same scenario, new text:

> Their version is **not safe** and risks hanging exactly the way the skill
> describes. The cell should read: `!lsdsk --report`

Second scenario, to check the ordinary case survived - somebody at a laptop
terminal with no idea what is wrong:

> `uvx lsdsk` ... this does **not** print text to scroll through ... it opens
> the full-screen TUI application in place

Still the interactive view, with `--report` offered as the alternative rather
than pushed. No regression.

## RED and GREEN diffed both ways

The RED run raised an exit-78 risk on a macOS runner that the first GREEN run
did not. Re-ran the same GREEN arm before treating it as a lost result: it came
back, along with a further point about an unprivileged run reading nothing. So
the absence was run-to-run variation, not displacement by the edit.

## Gaps reported by the test runs

| Gap                                                                                  | Outcome                                                                                   |
|--------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| Could not confirm where `--report` goes, or whether it combines with `--no-record`   | CLOSED. The flag's position and the refusal are now stated as a rule                      |
| Generalisation from the notebook to other unattended callers not separately measured | CLOSED. The text now says what was measured, and names the shared mechanism               |
| Unclear whether the pty behaviour is classic Jupyter only or every frontend          | CLOSED. Attributed to IPython's `!`, which every notebook frontend runs through           |
| Exit 1 does not distinguish a finding from an internal error                         | DECLINED. Pre-existing; the text already names `findings --format json` as the sharp test |
| IPython's own `_exit_code` semantics undocumented                                    | DECLINED. Not lsdsk's surface                                                             |
| Which page the interactive view opens on, and the full number-key mapping            | DECLINED. Pre-existing, and not what this edit is about                                   |

## Quote-back

Asked whether the text governs `lsdsk --report findings --format json`, an
isolated agent first answered **NONE** - the rule was written as one example
rather than as a rule, so it did not reach the form a reader would actually
type. Rewritten to quantify over every subcommand and any further options; the
retest quoted it:

> any `--report` with a subcommand after it - `findings`, `health`, `slots`, any
> of them, with or without further options - is refused with a usage error
> rather than ignored.

## Verification

- [x] RED run recorded verbatim, and it answers the question wrongly
- [x] GREEN run flips the answer, on the same scenario
- [x] The ordinary interactive case retested and unchanged
- [x] GREEN diffed against RED in both directions; the one absence re-tested and
      shown to be variation
- [x] Every reported gap closed or declined with a reason
- [x] Each fix verified by quote-back, one of which failed first and was rewritten
- [x] Every command the skill now promises was RUN against the built program:
      `lsdsk --report --replay <fixture>` exits 1 from the findings,
      `lsdsk --no-record --report --replay <fixture>` exits 1,
      `lsdsk --report findings` exits 2 as a usage error
- [x] No session narrative, no scratch paths, no machine-specific values
