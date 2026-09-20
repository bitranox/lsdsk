# Checklist - a crash stands when the reader has already gone

The pass before this one added `70` and left one question open on purpose: does a
crash stand over a departed reader, or yield to `141` as a verdict does? The user
answered it - `70` stands - so the ranking paragraph named an incomplete set.

## The defect this closes

The paragraph enumerated `2`, `13`, `22` and `78` as the codes that stand and
`0`/`1` as the codes that yield, and said nothing about `70`. A reader had to
infer, and the inference ran the wrong way.

## Change

- The ranking paragraph gains `70`, with its own reason rather than the refusals'
  one: a crash says nothing about what the output CONTAINED, so the reason a
  verdict yields does not reach it.
- It names the one crash that does NOT leave `70` - the one the departed reader
  CAUSED, where the failed write raises a broken pipe that carries its own code.
  Measured on the real functions: `BrokenPipeError` and `OSError(EPIPE)` both
  resolve to `141`, a `RuntimeError` to `70`.
- The `2` example gains how to observe it, from the GREEN gaps below.
- The constant behind all of this is `_TRUE_WHOEVER_WAS_READING`, renamed from
  `_RUN_DID_NOT_START`: a crash can happen mid-report, so the old name described
  only half of its members.
- No finding text, no link figure, no threshold value, no frontmatter moved.

## RED

Both arms ran through the inert probe type, which has no Bash, Read or Write.
One question: a wrapper runs `lsdsk findings --format json | head -1`, `head`
takes its line and leaves, and lsdsk then hits an internal error unrelated to the
pipe. What code does the wrapper see?

- [x] RED on the pre-change text answered `141`, and named the reason exactly:
      "`70` is conspicuously not in the 'stands' list - the doc names only `2`,
      `13`, `22`, `78` as immune". It is the wrong answer, reached correctly from
      what the text said.
- [x] GREEN on the new text answered `70`, quoting the new sentence, and applied
      the exception in the right direction - it used "unrelated to the pipe" to
      rule the `141` case OUT rather than in.
- [x] Both dispatches asked for a `Skill gaps` section, and both returned one.
- [x] GREEN diffed against RED in both directions. Nothing RED produced is missing
      from GREEN: RED's correct half was that `141` must not be read as healthy,
      and GREEN keeps it, as "the host's actual condition is simply unknown for
      this cycle, not clean".

## Skill gaps

GREEN reported three. One real and closed, two declined.

- [x] CLOSED: the ranking's own example, `lsdsk disks --bogus | head -c 0` leaves
      `2`, is not observable as written - a bare `$?` after a pipe is the READER's
      status, so a wrapper following the text literally reads `head` succeeding
      whatever lsdsk left. The example now says to read it through
      `${PIPESTATUS[0]}` or under `set -o pipefail`. This one matters beyond
      wording: every ranking rule in the paragraph is unobservable without it.
- [x] DECLINED: the composite case, an internal error whose own error path also
      attempts a write to the closed pipe. The answer is `70` - the write goes
      through the guard that exists for a departed reader, and the code the run
      decided survives the boundary flush - but spelling out a case that needs
      two coincidences would cost more attention than it returns, in a paragraph
      whose job is the rule.
- [x] DECLINED: what a wrapper should DO on each code beyond what it means -
      capture stderr, retry cadence, telling "still red" from "recovered". The
      table's job is what a code MEANS; a monitoring runbook is somebody else's
      document. Declined on the same ground as the equivalent gap in the previous
      pass.

## Left for another pass

Nothing from this pass. The question the previous one left open is closed here.
