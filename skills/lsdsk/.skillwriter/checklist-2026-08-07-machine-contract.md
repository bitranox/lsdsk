# skill-writer review: the machine-readable contract, and the in-process path

A code-quality sweep checked the shipped skill against the code by script. The
CLI surface was complete. Two things were not: the skill named the envelope's
four keys without saying what a FINDING looks like inside `data`, and said
nothing at all about using lsdsk from Python.

## RED

Two tasks, given to an isolated agent with the pre-change text: write a monitor
that fires only on CRITICAL, and read findings as objects from a snapshot
without shelling out.

It could do neither, and said so rather than inventing:

> the skill names no field for per-finding severity inside data. I cannot write
> `finding["severity"] == "critical"` without guessing a schema the text never
> gives.

> There is no mention anywhere in this skill of an importable Python package,
> module name, class, or function.

It also reached a conclusion that is true and that the skill left it to derive:
the exit code cannot express critical-only, because `1` means warning OR
critical.

## GREEN

Same two tasks, new text. Both answered with the code, quoting the skill for the
field name, the three permitted values, and the import lines.

## Gaps reported by the test runs

| Gap                                                                                                | Outcome                                                                                  |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| A monitor cannot tell a clean run from a BLIND one - the privilege signal was undocumented in JSON | CLOSED. `data.privileged` and `data.devices_accessible` are named, with what false means |
| `diagnose`'s other arguments unstated, so a caller silently gets shipped defaults                  | CLOSED. `thresholds` and `history` named, with what omitting them costs                  |
| `ok` and `skipped` semantics inferred rather than stated                                           | CLOSED in the same passage                                                               |
| No exception type documented for a bad snapshot in the library path                                | DECLINED. The CLI's exit codes cover the supported path; naming an internal exception    |
|                                                                                                    | would pin something no test holds                                                        |
| `set -o pipefail` not discussed                                                                    | DECLINED. Shell hygiene, not this tool's contract                                        |

## Verification

Every claim added was RUN against the built program, not reasoned about:

- [x] The documented monitoring pipeline, in BOTH arms: exit 1 on a critical
      finding, exit 0 when only warnings are present. No committed capture
      contains a critical finding, so the non-zero arm was driven with a
      synthetic payload; the suite covers the critical path itself in 15 places
      across three test modules, via domain objects rather than a fabricated
      capture.
- [x] The Python example, executed: 13 findings from a capture, all five
      attributes present.
- [x] The example was WRONG when first written - `snapshot.load` takes a `Path`
      and it passed a `str`. Running it is what caught that.
- [x] `data.privileged` and `data.devices_accessible` confirmed present in the
      `findings --format json` payload.
- [x] `diagnose`'s signature read from the code: returns a tuple, takes
      `history` and `thresholds` keyword-only.
- [x] Coverage re-checked by script after the edit: every CLI command and every
      `__all__` name appears.
- [x] No session narrative, no scratch paths, no machine-specific values.
