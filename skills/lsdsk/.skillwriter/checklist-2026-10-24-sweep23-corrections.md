# Checklist - the skill follows the sweep-23 fixes to the shortcut, the elevated copy and Spaces

## The change this documents

A set of code fixes made several sentences of the skill describe behaviour the tool no longer has,
and left new behaviour undocumented:

- `install-shortcut` names `user-dirs.dirs` as what turns the desktop off, and keeps
  `no desktop folder at <path>` for a folder that is really missing;
- a failed uv installer is reported by its own exit code (`78`), and the installer's output goes
  to standard error;
- a control character in a launch argument or the icon path, and a user configuration directory
  that cannot be named, are refused with `74`;
- the shortcut offer is silent in the elevated copy and in a run already holding root or
  Administrator, answers end of input as "not now" / "no", and names each failure in its own line;
- the elevated copy reads no `.env` unless `--env-file` names one;
- a Storage Spaces drive's use reads `-> F:` (no trailing separator), one use per Space;
- a subcommand's `-h`/`--help` prints while a configuration file is broken.

## Change

- `skills/lsdsk/SKILL.md`:
  - the no-desktop paragraph: the refusal is `<reason>, so there is nowhere to put a shortcut`,
    with both reasons quoted;
  - the `--install-uv` paragraph: the failed-installer and uvx-still-missing refusals, where the
    text goes (stderr `Error: <text>`, `error.message` under `--format json`), and the installer's
    output on standard error;
  - the outcome table: two `74` rows (control character, unnameable user configuration directory);
  - the offer: two silent cases, the three failure lines, end of input;
  - the `--elevated-relaunch` sentence: no offer, no `.env` unless `--env-file`;
  - the Storage Spaces use: `-> F:`, one `space` use per Space;
  - the `78` row: `config.toml` named beside `config.d`, and the help exception.

## Ground truth (run or read in the source)

- [x] Sandboxed runs (HOME, XDG_* under a temp dir):
  - `XDG_DESKTOP_DIR="$HOME/"` in `user-dirs.dirs`: rc 78,
    `Error: <config>/user-dirs.dirs turns the desktop off: its XDG_DESKTOP_DIR is the home folder
    itself, so there is nowhere to put a shortcut`.
  - relative `XDG_CONFIG_HOME`: rc 74, envelope `IO_ERROR`, the message on stderr as `Error: ...`
    and in `error.message`.
  - malformed `config.toml`: `config-deploy --help` and `disks -h` rc 0, `disks` rc 78.
- [x] Read in the source: `_installer_failed`, `_UVX_STILL_MISSING`, `_resolve_launch`,
  `_offer_create`, `_create_for_the_offer`, `_disable_for_the_offer` (adapters/cli/commands/
  shortcut.py); `run_installer` (stdout to fd 2, adapters/uv_bootstrap.py); `no_desktop_reason`
  (adapters/shortcut/linux.py, macos.py); `_string_value` refusing a control character
  (adapters/shortcut/linux.py); `user_config_path` (adapters/config/offer.py); `the_offer_is_made`
  and the relaunch marker in `resolve_tunables` (adapters/cli/commands/scan.py); `_dotenv_for`
  (adapters/cli/root.py); `space_of_each_member` (adapters/hw/windows/usage.py).

## RED

- [x] Route: a text check against the pre-edit skill FILE (`git show HEAD:skills/lsdsk/SKILL.md`),
      because the old version stays installed and a behavioural no-skill arm would load it.
      The pre-edit file stated `no desktop folder at <path>` for the `"$HOME/"` case, had no
      failed-installer case, no control-character or unnameable-path row, no elevated-copy or
      already-root silent case, no `.env` rule for the copy, `-> F:\` with the separator, no
      several-Spaces rule and no help exception (grep counts 0 for each new claim).

## GREEN and the re-probe loop (opus + haiku each round)

- [x] Round 1, ten questions (one per claim), both tiers: every answer quoted from the edited
      text, none NONE. 20 cells.
- [x] Round 2, the five questions the round-1 fixes touched, both tiers: every answer quoted.
      10 cells.
- [x] Round 3, the three questions the round-2 fixes touched (the manual install line under
      `--format json`, the general `74`/`78` rows, the failed-write JSON), both tiers: every
      answer quoted, no new undecided gap. 6 cells.
- [x] `tests/test_skill_describes_elevation.py::test_the_skill_names_the_relaunch_marker_only_as_internal`
      failed on round 1's text (the marker appeared in an offer bullet); the bullet no longer
      names the marker, and every test that reads SKILL.md passes (226).

| Gap                                                                                            | Decision                                                                             |
|------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| Where a refusal's text and the manual install line go under `--format json`                    | Closed: stderr `Error: <text>` and `error.message`                                   |
| The `78` row named only `config.d` as a malformed file                                         | Closed: `config.toml` or `config.d`                                                  |
| The control-character row read as covering macOS too, and did not name a newline               | Closed: "Linux `.desktop`", "a newline included"                                     |
| The general `78` row omitted a failed installer; the `74` row omitted the two refusals         | Closed: both rows name them                                                          |
| "The JSON is the `error` object alone" beside the envelope shape                               | Closed: "the `ok`-false envelope with its `error` and no `data`"                     |
| Human-mode stdout on a failure; a network request vs "downloads uv and nothing else"           | Declined: pre-existing, consistent with the command's tables                         |
| Failure-envelope keys (`command`, `skipped`); a control-character refusal under `74`, not `78` | Declined: the envelope section governs the keys; `74` is the code the command leaves |
| Which answer end of input gives at which question                                              | Closed: "not now" at the shortcut question, "no" at the uv question                  |
| `<config>` and `<reason>` placeholders not expanded; manual install line not quoted            | Declined: per-machine text, the placeholders say so                                  |
| How two uses are joined in one `used by` cell                                                  | Declined: the cell's general format, not this change                                 |
| Order of the no-desktop and unnameable-path checks; `--elevated-relaunch` typed by hand        | Declined: outside these fixes; the marker is documented as internal                  |
| Desktop-folder fallback chain read as falling through on a missing folder                      | Declined: pre-existing, and the no-desktop paragraph settles the case                |
| "Two opt-in paths" beside three subprocess paths in the intro                                  | Declined: the intro already names the relaunch as the one exception apart            |

## Quality

- [x] No frontmatter change. ASCII only (non-ASCII line count unchanged from HEAD). Tables
      reformatted.
- [x] No address, hostname or real path added; examples are `<path>`, `<config>`, `F:`.
