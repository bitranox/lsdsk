# Checklist - an access-denied Windows `.lnk` exits 13

## The change this documents

`install-shortcut` on Windows exits `13` (`PERMISSION_DENIED`) when Windows refuses to save the
`.lnk` with access denied (HRESULT `0x80070005`). Any other refusal still exits `74`. The skill's
outcome table said every `.lnk` refusal, "access denied included", exits `74`.

## Change

- `skills/lsdsk/SKILL.md`, "The desktop shortcut", outcome table:
  - the `13` row names a Windows `.lnk` refused with access denied (`0x80070005`), says the icon
    copy is covered "on any platform", and carries "(for a `.lnk`, the HRESULT in the message)";
  - the Windows `.lnk` `74` row is narrowed to "any reason other than access denied", so the two
    `.lnk` rows are disjoint.

## Ground truth

- [x] Read in the source: `adapters/shortcut/windows.py` `_write` catches `PermissionError`
      before `PwshPyError` and raises `ShortcutAccessDeniedError` (a `ShortcutWriteError` and a
      `PermissionError`, errno `EACCES`); `adapters/cli/commands/shortcut.py` `_write_shortcut`
      maps `PermissionError` to `PERMISSION_DENIED` and any other `OSError` to `IO_ERROR`.
- [x] Read in pwshpy 1.11.0: `new_shortcut`'s COM errors go through `marshal.native_error`, which
      raises `NativeAccessDeniedError` (a `NativeCallError` and a `PermissionError`) for
      E_ACCESSDENIED.
- [x] `tests/test_windows_shortcut.py`: an access-denied fake leaves exit `13` and
      `PERMISSION_DENIED` with the HRESULT in the message; an E_FAIL fake leaves exit `74`. Both
      new tests failed before the code change (exit `74`, `[Errno 5]`).
- [x] Run on a real Windows 11 machine with pwshpy 1.11.0, backup and restore privileges disabled:
      a folder denying Everyone write gave `ShortcutAccessDeniedError` caused by a real
      `NativeAccessDeniedError` (`hresult` `0x80070005`) and exit `13`, `error.type`
      `PERMISSION_DENIED`; the same folder without the deny wrote the `.lnk` and exited `0`.

## RED

- [x] `opus` probe on the pre-edit table (excerpt only, no tools). Asked: access-denied `.lnk`
      exit; E_FAIL `.lnk` exit; Windows icon copy refused for permission. Answered `74` for the
      access-denied `.lnk`, quoting "A Windows `.lnk` the shell refused to save, for any reason,
      access denied included". That is the defect: the text taught a code the tool no longer
      leaves. E_FAIL `74` and icon copy `13` were answered correctly.
- [x] Corpus check: no CLAUDE.md or memory fact names the `.lnk` access-denied exit code; the
      probe answered from the excerpt (it noticed a commit subject in its injected git context and
      still followed the excerpt), so the arm is not contaminated in the direction of the answer.

## GREEN

- [x] `opus` and `haiku` probes on the edited table, same three questions: both answered `13`
      `PERMISSION_DENIED` for access denied, `74` `IO_ERROR` for E_FAIL, `13` for the icon copy,
      each quoting the governing row verbatim. Tiers: `opus`, `haiku`; 2 arms x 3 questions.
- [x] GREEN diffed against RED in both directions: the two answers RED got right stay right in
      both GREEN arms; nothing RED answered was lost.

## Skill gaps (from every arm) and their disposition

- [x] "Access denied" was not defined (`opus` GREEN, `haiku` GREEN): CLOSED, the row names
      `0x80070005`. Re-probed by quote-back on the final text (below).
- [x] The icon-copy parenthetical could be read as Linux/macOS only (RED): CLOSED, "the icon copy
      on any platform".
- [x] No precedence between overlapping rows (RED, `opus` GREEN): CLOSED for the `.lnk` rows, now
      disjoint; the general `74` row and the `.lnk` `74` row give the same code, so no precedence
      is needed between them.
- [x] "Refused (any case above)" points outside the excerpt (all arms): DECLINED, covered
      elsewhere in the skill - the refusals are listed in the paragraphs directly above the table
      ("An account with no desktop folder gets no shortcut", "it refuses with `78` unless
      `--install-uv` is given").
- [x] Whether `data` is present on a failure (`opus` GREEN): DECLINED, covered elsewhere - the
      paragraph under the table says "the JSON is the `ok`-false envelope with its `error` and no
      `data`".
- [x] The HRESULT's format in the message, whether a refused `.lnk` leaves a partial file, and the
      icon-copy message content (`haiku` GREEN, `opus` GREEN): DECLINED, out of scope - the
      message text is pwshpy's, and none of the three is a contract this table states.

## Quote-back on the final text

- [x] `opus` at effort `medium`, given the final three `.lnk`-relevant rows, asked for the exit of
      a `.lnk` save failing with `0x80070005`: "Exit code 13", quoting "A write refused for
      permission: a Linux or macOS shortcut, a Windows `.lnk` refused with access denied
      (`0x80070005`), or the icon copy on any platform | `13`", and noting the `74` row covers only
      "any reason other than access denied".
- [x] Its gaps, both DECLINED: "no header row and no command name" is the probe's excerpt (the
      full table has its header and sits under "The desktop shortcut"); "matched exactly or by its
      Win32 part" is not a string match - the code decides by the error type pwshpy raises for
      E_ACCESSDENIED, and the row names the one value that type stands for. No new undecided gap.
