# Checklist - the skill describes the Windows desktop shortcut

## The change this documents

`install-shortcut` and the bare-`lsdsk` offer now work on Windows: they write `lsdsk.lnk` to the
Desktop Windows names for the account and to the Start-menu Programs folder, marked to run as
Administrator, with `lsdsk.ico`. The skill said Windows refused with exit `78` and that the offer
stays silent there.

## Change

- `skills/lsdsk/SKILL.md`, "The desktop shortcut" and the command line in "Running it":
  - the Windows refusal sentence is replaced by a Windows bullet: the two `.lnk` files, the
    known-folder Desktop (OneDrive included), both run as Administrator, one UAC prompt and one
    window, a cancelled prompt starts nothing, and a standard account runs lsdsk as the
    administrator who typed the credentials, with that account's configuration and history;
  - the icon copy (`lsdsk.png`, or `lsdsk.ico` on Windows, beside `config.toml`);
  - "replaces the files", and `data.shortcut` is the Desktop one;
  - the no-desktop refusal covers a Windows account with no Desktop, with its reason text;
  - the outcome table: the `13` row is narrowed to a Linux or macOS shortcut or the icon copy, and
    a new `74` row covers a `.lnk` the shell refused to save, access denied included;
  - the write order (Desktop first, menu second) holds for Windows too;
  - "on Windows" is gone from the offer's silent cases, and "holding Administrator" is pinned to an
    elevated process, not membership of the Administrators group.

## Ground truth

- [x] Read in the source:
  - `adapters/shortcut/windows.py`: `get_known_folder(DESKTOP)` and `(PROGRAMS)`;
    `new_shortcut(..., force=True, run_as_administrator=True)` for both files; a pwshpy error
    becomes `ShortcutWriteError` (an `OSError`, not a `PermissionError`), so the command maps it
    to `74`; `create` returns the Desktop path; a missing Desktop raises `FileNotFoundError`
    before anything is written.
  - `adapters/shortcut/icon.py`: `win32` copies `lsdsk.ico`, everything else the PNG.
  - `adapters/cli/commands/shortcut.py`: `shortcuts_for("win32")` returns the Windows adapter.
  - `adapters/hw/elevation.py`: `process_is_elevated` is Windows' own token check, which is why
    "Administrator" means an elevated process.
- [x] `tests/test_windows_shortcut.py` (14 tests, fake pwshpy module at the loader seam) holds each
      of those, and mutating `run_as_administrator` or the Start-menu write turns them RED.
- [x] Run on a real Windows 11 machine with pwshpy 1.9.0: `install-shortcut --format json` exited
      0 and named the Desktop file, and `ps.get_shortcut` read back both `lsdsk.lnk` files
      (Desktop and Start-menu Programs) with `run_as_administrator` true, the `uvx` target, the
      `"7 days"` argument quoted as one, the copied `lsdsk.ico` present, and the shared
      description.
- [x] A double-click on that machine, with lsdsk 1.15.6 on PyPI: one UAC prompt, then the
      interactive view in one window with no NOT ELEVATED banner and no second prompt; a cancelled
      prompt started nothing; the lsdsk icon on the Desktop and in the Start menu.

## RED

- [x] `redcheck --corpus-cascade .` reported STRONG inherited coverage on generic terms only
      (desktop, folder, offer, opens, programs) from the tree-top CLAUDE.local.md, which teaches no
      Windows shortcut. Route taken anyway: a quote-back check against the skill FILE, quote or NONE.
- [x] The pre-edit text went to probes on opus and haiku with five questions (the files written,
      UAC prompts and windows, the offer on Windows, the icon, the exit code of a refused save).
      Both tiers: Windows refuses with `78` and writes nothing; UAC and icon NONE; the offer is
      silent on Windows; a refused `.lnk` save is ambiguous between `13` and `74`.

## GREEN

- [x] The edited section went to opus and haiku with the same five questions plus "does a
      shortcut-started run offer again". Both tiers answered every question with a direct quote.

## REFACTOR

Gaps GREEN reported, and what happened to each:

- [x] Whether both `.lnk` files run as Administrator - CLOSED ("Both are marked to run as Administrator").
- [x] A standard account at UAC - CLOSED (runs as that administrator, with that account's
      configuration and counter history).
- [x] `13` and `74` both matching an access-denied `.lnk` - CLOSED (`13` row narrowed; the `.lnk`
      row says "access denied included").
- [x] One `data.shortcut` for two files - CLOSED ("`data.shortcut` is the Desktop one").
- [x] "Holding Administrator" read as group membership - CLOSED (an elevated process).
- [x] An access-denied icon copy exits `13` while an access-denied `.lnk` exits `74` - DECLINED
      in the text: the skill states both as they are. pwshpy's `NativeCallError` carries no HRESULT
      field, so lsdsk cannot tell access denied apart without parsing message text; a typed field
      is requested from pwshpy.
- [x] The Start-menu path is not in the JSON - DECLINED: the Linux menu copy is not reported
      either; `data.shortcut` names the Desktop file, which is the one the offer checks.
- [x] Only the Start-menu copy left, and the offer - DECLINED: covered, "once the shortcut exists
      (the file on the Desktop)".
- [x] The Windows user configuration path - DECLINED: `lsdsk config` names it; this section says
      only "beside `config.toml`".
- [x] `--install-uv` on Windows - DECLINED: covered, "the manual install line for the platform".
- [x] What Windows shows on a cancelled UAC prompt - DECLINED: Windows' own behaviour, not lsdsk's.
- [x] "Refused (any case above)" and "that key" unresolved - DECLINED: excerpt artifacts; the full
      section lists the cases and names `display.offer_desktop_shortcut`.

## Final quote-back

- [x] The final text of the touched paragraphs went to opus (effort medium) and haiku with five
      questions (both files run as Administrator; a standard account's configuration; access denied
      on a `.lnk`; an Administrators-group member at a normal prompt; which path `data.shortcut`
      reports). Both tiers quoted the governing line for all five; no new undecided gap - the two
      they raised (the icon-copy exit code and the unreported Start-menu path) are declined above.
- Tiers and cells: RED 2 tiers x 5 questions, GREEN 2 x 6, final quote-back 2 x 5.

## Quality checks

- [x] Present tense, no version history, no session narrative.
- [x] No address, host or private path added.
