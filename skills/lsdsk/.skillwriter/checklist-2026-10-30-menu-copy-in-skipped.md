# Checklist - install-shortcut names a failed menu copy in `skipped`

## The change this documents

When `install-shortcut` writes the Desktop shortcut but cannot write its copy into the
application menu (Linux) or the Start menu (Windows), `--format json` reports `ok` false and
names that copy in `skipped`, exit still `0`. Text output and the interactive offer print the
same sentence on stderr after `Warning: `. Before this, the skill described `ok` true, with only
a stderr warning that the JSON did not carry.

## Change

- `skills/lsdsk/SKILL.md`, "The desktop shortcut", outcome table:
  - the menu-copy row reads `ok` false, `data` as above, `skipped` naming the copy and the cause;
  - the `13` permission row names "the Linux or macOS Desktop shortcut" and "the Windows Desktop
    `.lnk`", so a refused menu copy matches the menu-copy row only;
  - the `74` HRESULT row names "The Windows Desktop `.lnk`".
- The paragraph under the table says the run still exits `0`, and gives the `skipped` list's one
  sentence for Linux (`... could not be added to the application menu at <path>: <cause>`) and
  for Windows (`... could not be added to the Start menu: <cause>`, the cause naming the `.lnk`
  path). It also gives the `Warning: ` stderr line, and says to branch on `ok` and `skipped`,
  never on the exit code.
- Offer paragraph: a Desktop shortcut whose menu copy failed prints `Shortcut created at <path>.`
  on stdout and then the same `Warning:` line on stderr.

## Ground truth

- [x] Run against the project CLI with an isolated HOME, XDG config, data and state dirs, and
      `$XDG_DATA_HOME/applications` a plain file. `--format json`: rc `0`, `ok` false,
      `data.shortcut` the Desktop path, `skipped` one sentence `the shortcut is on the Desktop,
      but it could not be added to the application menu at <path>/lsdsk.desktop: [Errno 17] File
      exists: ...`. Text: rc `0`, `Shortcut created:` on stdout, `Warning: the shortcut is on the
      Desktop, but ...` on stderr.
- [x] Read in the source: in `adapters/shortcut/linux.py` and `windows.py`, `create` returns
      `ShortcutWritten(path, skipped)`. In `adapters/cli/commands/shortcut.py`, `_report` passes
      `skipped=list(written.skipped)` to `emit_action`, which sets `ok = not skipped`, and
      `_warn_about_skipped` prints `Warning: <sentence>` with `err=True` for the text arm and the
      offer.
- [x] Windows sentence checked in the fake-pwshpy test: the cause is `[Errno 5] could not write
      <Programs>/lsdsk.lnk: <HRESULT text>`, which names the `.lnk` path.

## RED

- [x] Text check of the pre-edit file (`git show HEAD:skills/lsdsk/SKILL.md`). The menu-copy row
      read "`ok` true, `data` as above; the failure is one stderr warning only", and the paragraph
      said the warning is one "which `--format json` does not carry". A reader of that text
      branches on nothing and misses the missing menu entry. The tool now reports the opposite.

## GREEN

- [x] `opus` (effort `medium`) and `haiku`, the edited section pasted, five questions:
      - Linux menu copy blocked: `0`, `ok` false, one `skipped` sentence;
      - how a script notices: `ok` and `skipped`, not the exit code;
      - text mode: a stderr `Warning:` line;
      - full-disk Desktop write (control): `74`, `IO_ERROR`;
      - offer with a refused Start-menu copy: `Shortcut created at <path>.` then the stderr warning.

      Both arms answered all five with verbatim quotes. Tiers: `opus`, `haiku`; 2 arms x 5
      questions.
- [x] GREEN diffed against RED in both directions. The full-disk control is right in both
      arms, and nothing RED answered correctly was lost.

## Skill gaps (from every arm) and their disposition

- [x] The stream of `Shortcut created at <path>.` in the offer (both GREEN arms): CLOSED, "on
      stdout".
- [x] The exact `skipped` sentence and the list shape (`opus` GREEN): CLOSED. The paragraph gives
      both sentences and "a list holding one sentence".
- [x] A Start-menu `.lnk` refused with access denied matched both the `0` row and the `13` row
      ".lnk refused with access denied" (both GREEN arms): CLOSED, the `13` and `74` `.lnk` rows
      name "the Windows Desktop `.lnk`".
- [x] "Succeeds" beside `ok` false reads as two claims (`opus` GREEN): CLOSED, "the run still
      exits `0`".
- [x] A Linux menu copy refused for permission matched the `13` row's "a Linux or macOS shortcut"
      (`opus` re-probe): CLOSED, "the Linux or macOS Desktop shortcut".
- [x] "Refused (any case above)" overlaps every row (all arms): DECLINED, covered elsewhere.
      The refusals it means are listed in the paragraphs directly above the table, which the
      probe excerpt left out. This predates the change.
- [x] "Any other failed write" (`74`) against the menu-copy row (`0`) (final re-probes):
      DECLINED, covered elsewhere. The paragraph under the table says "a failure on the menu copy
      leaves the Desktop file in place and the run still exits `0`", and the menu row reads "for
      any reason".
- [x] The icon copy's place in the write order (`opus` final): DECLINED, covered elsewhere. "The
      icon is copied before either" is in the paragraph, which the excerpt cut off.
- [x] `skipped` on success and in the error envelope (`opus` re-probe): DECLINED, covered
      elsewhere. The envelope section says "`skipped` is a list of sentences saying what was not
      done and why, empty when nothing was".
- [x] The `<cause>` format, and capitalisation after `Warning: ` (re-probes): DECLINED. The
      sentence is quoted literally, and the cause is the operating system's error text, which
      the skill does not fix.
- [x] macOS menu copy (all arms): DECLINED, covered elsewhere. The macOS bullet says the
      launcher goes on `~/Desktop` only.
- [x] The offer's exit code (`haiku` GREEN): DECLINED, covered elsewhere. "The view opens in every
      case".
- [x] "Its exit code is never a verdict about the hardware" beside the `13`/`74` rows (`haiku`
      re-probe): DECLINED, out of scope. That sentence predates this change and is about
      hardware, not the write.

## Quote-back on the final text

- [x] `opus` (effort `medium`) and `haiku`, the final table, three questions:
      - a Linux menu copy refused with EACCES: `0`, `ok` false, quoting the menu-copy row. Both
        said the `13` row does not cover it, quoting its "Desktop shortcut" list;
      - a Linux Desktop entry refused with EACCES: `13`, `PERMISSION_DENIED`, quoting the row;
      - overlapping rows: only the declined ones above, which are excerpt artifacts.

      No new undecided gap.
