# Checklist - the skill states where the desktop shortcut is refused, and what it writes

## The change this documents

`install-shortcut` has no Windows adapter in this build, and since 1.15.1 it refuses on an account
with no desktop folder. The skill listed the command as if it worked everywhere and said nothing
about either refusal, the files it writes, its exit codes or the bare-`lsdsk` offer.

## Change

- `skills/lsdsk/SKILL.md`:
  - New subsection "The desktop shortcut" under "Running it". It covers:
    - the platforms, and the Windows refusal (`78`, before anything else);
    - the files written on Linux and macOS, and how the Linux Desktop is resolved;
    - the launcher and its cooldown, and whose Desktop the shortcut lands on;
    - the no-desktop refusal (`78`, writes nothing, never creates the folder, refuses before `--install-uv`);
    - `--install-uv` as the consent;
    - an outcome table (`0` / `78` / `13` / errno with no envelope);
    - write order on Linux;
    - when the bare-`lsdsk` offer is silent, and what its three answers do.
  - The command's line in "Running it" now says Linux/macOS.
  - `install-shortcut` was added to these places:
    - the write-by-design list for read-only pipelines;
    - the not-a-verdict paragraph;
    - the `0`, `13` and `78` rows of the exit-code table, plus an exception note on the `70` and `74` rows.
  - Two sentences that were already in the file and contradicted the shipped tool were corrected:
    - the intro's uv-installer condition, which named the offer path only and said "no `uvx`" where the code tests for neither `uvx` nor `lsdsk`;
    - the "does not go online" section, which named only the Graylog sink.
  - The "does not know which pool a disk belongs to, whether a drive is a boot device" claim was
    stale against the `used by` column. It now says what the column names and what layout stays
    unknown (the RAID level or vdev arrangement, cache or spare roles).

## Ground truth (every stated behaviour was run or read in the source, not inferred)

- [x] Sandboxed runs (HOME, XDG_* under a temp dir):
  - Desktop missing: rc 78, `no desktop folder at <path>, so there is nowhere to put a shortcut`; with `--format json` an `ok` false envelope, `error.type` `CONFIG_ERROR`. No folder and no menu copy were created.
  - Desktop not writable: rc 13, one `PermissionError` stderr line; `--format json` printed no envelope.
  - Menu dir not writable: rc 13, and the Desktop file stayed.
  - A directory where the file goes: rc 21 (the errno), no envelope.
  - Success: rc 0, `data.shortcut` and `data.launch`. Both files were written, executable.
- [x] Read in the source:
  - Windows refusal: `shortcuts_for` returns None, then `fail(..., CONFIG_ERROR)` before the desktop check.
  - Offer gating: `should_offer`, `offer_shortcut`, and its call before `ask_before_the_view` in `run_default_view`. That call is not reached on the page branch, and `cli_tui` never calls it.
  - Launcher preference: `plan_launch`.
  - Cooldown exemptions: `uvx_launch_args`, `OWNED_CLOSURE`.
  - Opt-out target: `disable_offer`.
  - Installer result: `_UVX_STILL_MISSING` maps to 78.

## RED

- [x] `redcheck --corpus-cascade .` reported STRONG inherited coverage of the headless half (a memory
      fact and the repo's CLAUDE.local.md), so the behavioural arm cannot fail honestly. Route
      taken: a quote-back check against the skill FILE, quote or NONE.
- [x] The pre-edit text (`git show HEAD:skills/lsdsk/SKILL.md`) went to probes on opus and haiku,
      four questions each:
      - Windows behaviour;
      - no desktop folder;
      - when the offer is silent;
      - which files.
      Both tiers answered NONE to all four.

## GREEN and the re-probe loop (opus + haiku each round, 10 cells)

- [x] GREEN: both tiers answered all six questions by direct quote, adding the read-only pipeline
      and exit 13.
- [x] Round 1 on the final text: ten questions, all quoted on both tiers.
- [x] Rounds 2 to 4 re-asked the questions each fix touched. Round 4: both tiers quoted every
      answer, and neither found a sentence that contradicts the shortcut table.
- [x] Each round's gaps fed the next edit. The table records the important ones.

| Gap                                                                                                                  | Decision                                                                                                       |
|----------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| Exit codes for a failed write unstated, then "non-zero, not 74" not actionable                                       | Closed: measured, written as the errno (`21` example), stderr line to read                                     |
| My own first edit put `install-shortcut` beside "13 / 74 for any other reason"                                       | Closed: the paragraph separates the command and points at its table                                            |
| General rows `70` and `74` state 74 for every failed write                                                           | Closed: exception notes on both rows                                                                           |
| Intro and "does not go online" each named a different uv path, with a wrong condition                                | Closed: both now name both paths and the neither-`uvx`-nor-`lsdsk` condition                                   |
| Offer: once, not now, decline, failure, `--replay`, `--no-elevate`, uv already present                               | Closed in the offer paragraph                                                                                  |
| No-desktop case on macOS, `XDG_DESKTOP_DIR` missing, menu copy, whose Desktop under sudo                             | Closed                                                                                                         |
| Windows plus `--install-uv` order                                                                                    | Closed: refuses before anything else                                                                           |
| Shortcut launch fetches from PyPI                                                                                    | Closed: named as uv's request; cooldown exemption of lsdsk and its own libraries stated                        |
| Errno can collide with `2` / `22` / `1`, no JSON envelope on a failed write, row `1` and one prose line still say 74 | Declined here: this is a code defect, filed as lsdsk OPEN-WORK [120]; the fix makes these rows true as written |
| Installer's own exit codes, URL pinning, exact text of the offer's failure line                                      | Declined: installer internals, not usage the skill teaches                                                     |
| A macOS double-click without a hardware reader, offer when bare `lsdsk` starts as root, stdout refusing              | Declined: outside this change; existing privilege and macOS text governs                                       |

## Quality

- [x] Every test that reads SKILL.md (found with a grep of `tests/` for the filename) passes: 192.
      An earlier draft named `HOME` and `XDG_DESKTOP_DIR` bare and failed
      `test_every_constant_the_skill_names_is_one_this_package_has`; both are written with `$` now.
- [x] No frontmatter change. ASCII only, apart from the box drawing already in HEAD. Tables reformatted.
- [x] No address, hostname or real path added; the examples are `~/Desktop`, `~/Schreibtisch` and
      `<path>`.
