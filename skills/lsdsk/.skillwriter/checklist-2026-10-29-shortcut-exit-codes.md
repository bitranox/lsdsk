# Checklist - install-shortcut: a relative config dir exits 78, a failed menu copy exits 0

## The change this documents

`install-shortcut` with a relative `$XDG_CONFIG_HOME` exits `78` (`CONFIG_ERROR`) naming the
relative path, where the skill said `74` (`IO_ERROR`). When the Desktop shortcut is written but the
copy into the application menu (Linux) or the Start menu (Windows) fails, the command succeeds:
exit `0`, `ok` true, one stderr warning, nothing in the JSON. The skill said such a failure "exits by
the row that failure matches".

## Change

- `skills/lsdsk/SKILL.md`, "The desktop shortcut", outcome table:
  - the relative `$XDG_CONFIG_HOME` row moves to `78`, `CONFIG_ERROR`, "the message naming the
    relative path", directly under the `Refused` row;
  - a new row: Desktop shortcut written, menu copy failed for any reason, a refused permission
    included, `0`, `ok` true, the failure one stderr warning only.
- The paragraph under the table: a failed menu copy leaves the Desktop file and the command
  succeeds (exit `0`, `ok` true, one stderr warning `--format json` does not carry); "Any other
  failure ends the run" replaces "The first failure ends the run".
- Exit-code table: the `74` row no longer lists "a user configuration directory that cannot be
  named"; the `78` row's `install-shortcut` refusals add "a relative `$XDG_CONFIG_HOME`, which names
  no user configuration directory".

## Ground truth

- [x] Run against the installed CLI with an isolated home, `XDG_CONFIG_HOME=relcfg`: rc `78`,
      `{"ok":false,...,"error":{"type":"CONFIG_ERROR","message":"the shortcut was not written: the
      user configuration directory relcfg/lsdsk is not an absolute path; ..."}}`.
- [x] Same isolated home with `$XDG_DATA_HOME/applications` a plain file: rc `0`, `ok` true,
      `data.shortcut` the Desktop path, `skipped` `[]`, one `[WARN]` line on stderr naming the menu
      path and `[Errno 17] File exists`; the Desktop entry is present.
- [x] Read in the source: `adapters/shortcut/linux.py` `_add_to_the_menu` and
      `adapters/shortcut/windows.py` `_add_to_the_start_menu` catch every `OSError` (a
      `PermissionError` included) and report it through `warn`.

## RED

- [x] `opus` probe on the pre-edit text (excerpt only, no tools), three questions: relative config
      dir, menu copy failing on a plain file, newline in a launch argument. Answered `74`
      `IO_ERROR` for the relative config dir, quoting the old row, and `74` with no `data` for the
      menu copy, quoting "exits by the row that failure matches". Both are codes the tool no longer
      leaves. The newline control answered `74`, correctly.

## GREEN

- [x] `opus` and `haiku` probes on the edited text, same three questions: `78` `CONFIG_ERROR`;
      `0`, `ok` true, nothing in the JSON; `74` `IO_ERROR`. Each quoted the governing line
      verbatim. Tiers: `opus`, `haiku`; 2 arms x 3 questions.
- [x] GREEN diffed against RED in both directions: the newline answer RED got right stays right in
      both arms; nothing RED answered correctly was lost.

## Skill gaps (from every arm) and their disposition

- [x] "The first failure ends the run" read as contradicting the menu-copy rule (`opus` GREEN):
      CLOSED, "Any other failure ends the run".
- [x] "The message naming the directory" was unclear for a directory that cannot be named (`opus`
      GREEN): CLOSED, "the message naming the relative path".
- [x] No table row for a failed menu copy, so a reader using the table as a lookup got `13` or
      `74` for a refused or full-disk menu copy (quote-back `opus`): CLOSED, the new `0` row naming
      "for any reason, a refused permission included".
- [x] "Refused (any case above)" points outside the excerpt (all arms): DECLINED, covered
      elsewhere - the refusals are listed in the paragraphs directly above the table.
- [x] Whether a newline in a launch argument is refused on Windows or macOS (`opus` GREEN, RED):
      DECLINED, out of scope - the row is about the Linux `.desktop` format and predates this
      change.
- [x] Whether the control-character check runs before the icon copy (`opus` GREEN): DECLINED, out
      of scope - the row's "nothing written" predates this change and was not touched.
- [x] Whether `data.shortcut` names the menu path (`opus` GREEN) and what "data as above" holds
      (`haiku` final): DECLINED, covered by the first row, which names `data.shortcut` (the path)
      and `data.launch`.
- [x] That `--format json` carries no trace of a failed menu copy (quote-back `opus`): DECLINED
      here, the text states it outright; whether the envelope should list it in `skipped` is a
      code question, recorded in the project backlog.

## Quote-back on the final text

- [x] `opus` at effort `medium`, final text, asked whether a failed menu copy ends in `ok` false
      and what the relative-path message names: succeeds with `0`, quoting the paragraph; the
      relative path, quoting the row. Its one new gap (no table row for the menu copy) is closed
      above.
- [x] `haiku`, final table, asked for a menu copy refused for permission: `0`, `ok` true, quoting
      the new row, and reading the `13` row as scoped to the shortcut write itself. No new
      undecided gap.
