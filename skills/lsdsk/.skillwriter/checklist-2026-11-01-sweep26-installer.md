# Checklist - install-shortcut: an installer that cannot start, and the user-directory refusal

## The change this documents

"The desktop shortcut" section of `skills/lsdsk/SKILL.md` now matches the tool after sweep 26:

- The uv installer's own shell (`sh`, or `powershell` on Windows) is started only from an
  absolute PATH directory, and the installer runs with only the absolute PATH entries. With no
  such shell, nothing runs and the command refuses with `78` and `the uv installer could not
  start: no 'sh' in an absolute PATH directory, so there is no launcher for the shortcut`, followed
  by the manual install line, like the `failed (exit N)` refusal.
- The `78` row for a user configuration directory that cannot be named now also covers Windows (a
  relative `%APPDATA%`, or a relative `%LOCALAPPDATA%` where `%APPDATA%` does not exist), says it
  is refused before `--install-uv` could install anything, and says the message names the variable
  to set.
- The offer: a failed install prints the command's own message (either form), manual install line
  included and without the `Error:` prefix, and writes no shortcut.
- The offer paragraph is re-wrapped at 100 columns, with no words changed (asserted when it was
  re-wrapped).

## Ground truth

- [x] The real CLI in an isolated HOME with its own XDG dirs:
      - PATH without `sh` in any absolute directory, `--install-uv`, no uv: rc `78`,
        `CONFIG_ERROR`, `the uv installer could not start: no 'sh' in an absolute PATH directory,
        so there is no launcher for the shortcut. Install uv yourself with: curl -LsSf
        https://astral.sh/uv/0.13.0/install.sh | sh then run 'lsdsk install-shortcut'.`, Desktop
        empty;
      - `XDG_CONFIG_HOME=relcfg` with `XDG_DESKTOP_DIR` set, `--install-uv`: rc `78`, `the shortcut
        was not written: the user configuration directory relcfg/lsdsk is not an absolute path; set
        XDG_CONFIG_HOME to an absolute path or unset it`, no install attempted, Desktop empty.
- [x] `user_config_path(platform="win32")` measured: `APPDATA=rel` (existing) refused naming
      `APPDATA`; `APPDATA` missing with `LOCALAPPDATA=rel2` refused naming `LOCALAPPDATA`; a
      relative `APPDATA` that does not exist with an absolute `LOCALAPPDATA` is NOT refused (the
      library falls back), which is why the row says "where `%APPDATA%` does not exist".
      `platform="darwin"` with `HOME=relhome` names `HOME`. (Commit 02bba87, S26-14.)
- [x] Read in the source: `_installer_failed` in `adapters/cli/commands/shortcut.py` is the one
      message for the command and the offer; the offer prints it with `err=True` and no prefix and
      returns before any shortcut is written.

## RED

- [x] Text check of the pre-edit file (`git show HEAD:skills/lsdsk/SKILL.md`): 0 hits for `could
      not start` and 0 for `absolute PATH directory`; the config-directory row named
      `$XDG_CONFIG_HOME` only and did not say it is refused before an install; the offer named only
      `the uv installer failed (exit N)`.

      A reader of that text predicts an installer launched through `.`, and on Windows no refusal
      for a relative `%APPDATA%`. The tool now does neither.

## GREEN

- [x] `opus` and `haiku`, the edited section pasted in: six questions (`opus`) and four (`haiku`):
      `sh` only through a relative entry; installer exiting 3; relative `XDG_CONFIG_HOME` with
      `XDG_DESKTOP_DIR` set; Windows `APPDATA` missing plus relative `LOCALAPPDATA`; the offer with
      an installer that cannot start; whether the installer gets relative PATH entries.

      Both arms answered every question as the CLI behaves, each with a verbatim quote.

## Skill gaps (from every arm) and their disposition

- [x] What the offer prints after a failed install, with or without `Error:`, and whether a
      shortcut is written (`opus`): CLOSED, "prints the command's own ... message, manual install
      line included and without the `Error:` prefix, and writes no shortcut" (shortcut.py, the
      offer's install branch).
- [x] `haiku` gave the offer an exit `78`: DECLINED, excerpt artifact. Its excerpt left out "The
      view opens in every case", which the final quote-back then answered correctly.
- [x] The exact manual install line (all arms): DECLINED. It carries the pinned uv version, which
      changes with each uv bump; the skill says it follows the message.
- [x] The exact variable named for each platform (`opus`): DECLINED. The row says the message
      names it; the variable per platform is the code's choice and is held by
      `tests/test_a_linked_user_config.py`.
- [x] The order of the config-directory check against the launcher check (`opus`): DECLINED, as in
      the sweep-25 checklist. Every such row says it is refused before any install, so the order
      changes no outcome a reader acts on.
- [x] "Does not map a missing shell to `could not start`" (final `haiku` quote-back): DECLINED,
      excerpt artifact. The command paragraph states it.

## Quote-back on the final text

- [x] `haiku`, the re-wrapped offer paragraph, three questions (installer cannot start, installer
      exits 6, whether the view opens): correct with quotes. No new undecided gap. Tiers: `opus`,
      `haiku`. Cells: 1 x 6 + 1 x 4 GREEN, 1 x 3 quote-back.

## Mechanical checks

- [x] The width and fence check over the added prose lines prints nothing.
- [x] Every test that reads SKILL.md (`grep -rl SKILL.md tests/ --include=*.py`, 9 files): 255
      passed.
