# Checklist - install-shortcut: launcher lookup, relative XDG, non-UTF-8, offer messages

## The change this documents

"The desktop shortcut" section of `skills/lsdsk/SKILL.md` now matches the tool:

- `uvx` or `lsdsk` counts as on PATH only in an absolute PATH directory; one reachable only
  through the current folder or a relative entry counts as not installed. `data.launch` starts
  with the launcher's absolute path.
- The no-launcher refusal writes nothing, and its message starts `uv is not installed, so there is
  no launcher for the shortcut` and ends naming `--install-uv`.
- New `78` row: on Linux, a relative `$XDG_DESKTOP_DIR` or `$XDG_DATA_HOME`, refused before
  `--install-uv` could install anything, with the message naming the variable and nothing written.
- New `74` row: on Linux or macOS, a launch argument or the icon path holding a byte that is not
  valid UTF-8, with the message saying it `is not valid UTF-8` and nothing written.
- The Windows `skipped` sentence for a failed Start-menu folder lookup.
- A failed write's stderr line is `Error: <text>` naming the file.
- The offer: an icon that will not copy does not stop it. It writes the shortcut without an icon,
  prints `Shortcut created at <path>.` on stdout, then `Warning: the shortcut was created without
  its icon, which could not be copied: <error>` on stderr. Its failed-install line and both
  `Could not ...` lines go to stderr.
- The launcher paragraph is re-wrapped at 100 columns, with no words changed (asserted when the
  paragraph was re-wrapped).

## Ground truth

- [x] The real CLI, `python -m lsdsk install-shortcut --format json`, each case in an isolated
      HOME with its own XDG config, state and data dirs:
      - relative `XDG_DATA_HOME`: rc `78`, `CONFIG_ERROR`, `the shortcut was not written:
        XDG_DATA_HOME=rel-data is not an absolute path; ...`, no `lsdsk.desktop` anywhere;
      - relative `XDG_DESKTOP_DIR`: the same, naming `XDG_DESKTOP_DIR`;
      - `uvx` only in the cwd and in `bin/` reached through PATH entries `bin`, `.` and an empty
        entry: rc `78`, `CONFIG_ERROR`, message starting `uv is not installed, so there is no
        launcher for the shortcut.` and ending `Re-run with --install-uv to have lsdsk install uv
        for you first.`, nothing written;
      - read-only Desktop: rc `13`, `PERMISSION_DENIED`, message naming `Desktop/lsdsk.desktop`;
      - the only `uvx` in an absolute PATH directory whose name holds byte 0xff: rc `74`,
        `IO_ERROR`, the argv quoted starting with that absolute path and ending `is not valid
        UTF-8, which a desktop entry cannot carry`.
- [x] Read in the source:
      - `adapters/cli/commands/shortcut.py` builds the no-launcher message in one place for every
        platform (`uv is not installed ...` then `Re-run with --install-uv ...`);
      - the offer prints `Could not create the desktop shortcut`, `Could not save the
        do-not-offer setting` and the failed-install line with `err=True`;
      - the offer prints `Shortcut created at <path>.` on stdout and the icon warning with
        `err=True`, from `_icon_or_reason`;
      - `adapters/shortcut/windows.py` returns `the shortcut is on the Desktop, but Windows could
        not name the Start menu folder: <error>` when the folder lookup fails;
      - `adapters/shortcut/macos.py` refuses a non-UTF-8 argument the same way as Linux.

## RED

- [x] Text check of the pre-edit file (`git show HEAD:skills/lsdsk/SKILL.md`):
      - the only relative-XDG row named `$XDG_CONFIG_HOME`;
      - "on PATH" had no qualifier, so a `uvx` found through `.` read as installed;
      - the `74` refusal named control characters only;
      - the paragraph said "an icon that will not copy writes no shortcut at all" with no offer
        exception;
      - no sentence covered a failed Start-menu lookup.

      A reader of that text predicts a menu copy written into a relative folder, a shortcut
      launched through the current folder, and no shortcut from an offer whose icon would not
      copy. The tool now does none of these.

## GREEN

- [x] `opus` (effort `medium`) and `haiku`, the whole edited section pasted in, six questions:
      - relative `XDG_DATA_HOME`;
      - `uvx` only through `./` and the `bin` entry;
      - macOS launcher directory holding 0xff;
      - Windows Start-menu lookup failure;
      - offer with the icon copy refused;
      - Linux Desktop write refused (control).

      Both arms answered all six as the CLI behaves, each with a verbatim quote.
- [x] GREEN diffed against RED in both directions: nothing the old text decided correctly was lost.
      The `13` permission control is unchanged in both.

## Skill gaps (from every arm) and their disposition

- [x] Whether `data.launch` holds the launcher's absolute path, which decides whether a 0xff
      directory reaches a launch argument (both arms): CLOSED, "the exact argv, starting with the
      launcher's absolute path". Ground truth: the 74 message quotes the absolute path.
- [x] The no-launcher refusal had no message text and did not say whether anything is written
      (`opus`, `haiku`): CLOSED, "writing nothing; the message starts `uv is not installed, so
      there is no launcher for the shortcut` and ends naming `--install-uv`".
- [x] The stream of `Shortcut created at <path>.` in the iconless case (both arms): CLOSED, "on
      stdout".
- [x] Whether a failed write's stderr line uses the `Error:` prefix (`opus`): CLOSED, "one stderr
      line `Error: <text>` naming the file". Ground truth: the read-only Desktop run.
- [x] The streams of the offer's failed-install and `Could not ...` lines (`opus` re-probe): CLOSED,
      "The failed-install line and both `Could not ...` lines go to stderr" (shortcut.py,
      `err=True` on each).
- [x] The order of the relative-XDG, launcher and UTF-8 checks (both arms): DECLINED, judgement
      left open. Every one of those rows says nothing is written, so the order changes no outcome
      a reader acts on.
- [x] The wording of `<cause>`, `<text>` and `<error>` (all arms): DECLINED. It is the operating
      system's error text, which the skill does not fix, as in the 2026-10-30 checklist.
- [x] "Refused (any case above)" overlaps the specific `78` rows (`opus`): DECLINED, covered
      elsewhere. The paragraphs above the table list the refusals it means. This predates the
      change.
- [x] What an iconless Windows `.lnk` looks like, and whether the menu copy is still made when
      the icon fails (`opus`): DECLINED, out of scope. The offer writes "the shortcut" through the
      same create as the command. Its appearance is the shell's choice.
- [x] The two elided sentences, and the offer list, in the re-probe excerpt (re-probes): DECLINED,
      excerpt artifacts. Both are in the section (the `skipped` sentences and the offer answers).

## Quote-back on the final text

- [x] `opus` (effort `medium`) and `haiku`, the final text, four questions:
      - the relative-PATH `uvx` (exit, nothing written, message start and end);
      - the 0xff launcher directory;
      - the streams in the iconless offer;
      - a full-disk Desktop write.

      Both arms were correct with quotes.
- [x] `haiku` quote-back on the offer-streams sentence, three questions: correct.

      No new undecided gap. Tiers: `opus`, `haiku`. Cells: 2 x 6 GREEN, 2 x 4 re-probe,
      1 x 3 quote-back.

## Mechanical checks

- [x] The width and fence check over the added lines prints nothing. The launcher paragraph was
      re-wrapped whole, because fixing one wide line at a time pushed the overflow onto the next
      line.
- [x] The version-history finder over the added lines: one hit, an existing table row the
      formatter re-padded, not history.
- [x] Every test that reads SKILL.md (`grep -rl SKILL.md tests/`): 242 passed.
