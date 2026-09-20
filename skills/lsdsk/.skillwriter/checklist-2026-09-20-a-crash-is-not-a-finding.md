# Checklist - exit `70`, so a crash stops reading as a machine that needs attention

`ExitCode.SOFTWARE_ERROR = 70` exists in the tool now. The skill's exit-code table
said the opposite of what is true: that an internal error also leaves `1`.

## The defect this closes

Exit `1` meant two things at once - a reporting command found a warning or a
critical, and this tool broke - so a monitoring check could not tell a failing
drive from a bug. The only remedy the table could offer was "read stderr before
treating it as a finding", which is an instruction a check cannot follow.

## Change

- The paragraph above the table says an internal error is NOT one of the things
  `1` can mean, so a check can act on `1` as a verdict without first reading
  stderr to find out whether the tool merely broke.
- The `1` row drops "An internal error also leaves `1`" and gains "NOT a crash -
  that is `70`".
- A `70` row: an exception no command handled, a bug to report and never a
  statement about the machine, routed to whoever owns the tool rather than to
  storage. It carries the rule that keeps it honest - an `OSError` keeps its own
  code, because EPERM is itself `1`, so the split is on where the code came from
  and never on the number.
- One sentence on what a crash leaves on each stream, from the GREEN gaps below.
- No finding text, no link figure, no threshold value, no frontmatter moved.

## RED

Both arms ran through the inert probe type, which has no Bash, Read or Write, so
neither could reach the repo. Same two questions each time: a fleet check that
records only the exit code meets `70` on one host and `1` on another, and a
paging policy is written from the answers.

- [x] RED on the pre-change text could answer NEITHER question. On `70`: "not
      among them... Nothing in the text maps `70` to any lsdsk-defined
      condition", so it returned "cannot tell". On `1`: "consistent with two
      different situations", and the check "has literally discarded the one piece
      of information the documentation says is required to disambiguate".
- [x] GREEN on the new text answered both definitively and correctly: `70` is
      "not a statement about the drive/machine at all... Route this to whoever
      owns `lsdsk`", and `1` is a verdict to page on, with the `record`/`snapshot`
      failed-write clause correctly excluded because the command was `health`.
- [x] Both dispatches asked for a `Skill gaps` section, and both returned one.
- [x] GREEN diffed against RED in both directions. Nothing the baseline produced
      is missing from GREEN: RED's one correct result was the `1` ambiguity,
      which GREEN reproduces as the now-resolved half.

## Skill gaps

GREEN reported four. One real and closed, three declined.

- [x] CLOSED: the text did not say whether a `--format json` run still writes an
      error envelope before dying with `70`, or whether stdout is empty.
      Measured: stdout holds 0 bytes, the exception is on stderr, and the
      last-resort handler never reads `--format` at all - the only format test on
      that path belongs to the usage-error branch. Stated now, with the one case
      that is not empty: a crash that landed mid-write leaves a truncated report.
- [x] DECLINED: "did not consult the live source tree... if the real exit-code
      table in `exit_codes.py` disagrees with this excerpt". The excerpt-only
      constraint was the test condition, not a gap in the text. The two agree;
      `test_the_exit_codes_the_docs_promise_are_the_ones_the_code_defines` is
      what holds them together, and it carries `70` now.
- [x] DECLINED: "does not state whether `lsdsk health` can exit `13` instead of a
      findings code". The full `13` row says "a diagnostic run whose hardware read
      the kernel refused outright", which is that case; the excerpt abbreviated
      that row.
- [x] DECLINED: warning-versus-critical is not recoverable from the exit code.
      True and deliberate - the code is a binary verdict. The skill already gives
      the split from the JSON body, both as a shell one-liner over
      `data.findings` and as `count_by_severity`.

## Left for another pass

- Whether `70` should outrank a departed reader is undecided. It is deliberately
  NOT in `_RUN_DID_NOT_START`, so a crash whose reader has gone answers `141`
  exactly as a finding does. That is a contract question for the user rather than
  an implementation detail, and settling it inside this change is the mistake the
  ranking work already recorded. Tracked separately.
