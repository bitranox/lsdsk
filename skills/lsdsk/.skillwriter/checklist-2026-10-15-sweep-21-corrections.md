# Checklist - exit 22 for config-deploy, `space_is_quota`, the narrowed `zfs-pool-disks`, and unplaced-volume names

## The change this documents

Four behaviours of the code the skill described wrongly or not at all:

- `config-deploy` refuses an unsafe or unreadable permission mode with exit 22, whether configured,
  given with `--set` or given as `--dir-mode`/`--file-mode`, and a `--set` of
  `lib_layered_config.default_permissions` that is not a table; `--dir-mode`/`--file-mode` text that
  is not plain octal is exit 2. The 22 row said `--set` never produces 22.
- Every filesystem row carries `space_is_quota` (`src/lsdsk/domain/models.py`, `Filesystem`).
- `zfs-pool-disks` is raised only for a listed pool that a reading which was not taken could have
  placed; a pool on files or virtual devices has no row and no line
  (`src/lsdsk/adapters/hw/linux/filesystems.py`).
- A Windows volume without a path is named `volume <n> on <disk>` in its `volume` refusal, `<n>`
  being its 0-based place among the capture's volumes (`src/lsdsk/adapters/hw/windows/filesystems.py`).

## Change

- The exit-22 row names the `config-deploy` refusal and separates `--set` (2 when it cannot be
  placed, 22 when `config-deploy` refuses its mode).
- The `config-deploy` paragraph states which modes are refused and with which code, and that Windows
  reads no configured mode.
- The `data.filesystems` field list gains `space_is_quota` with one sentence on what it means for
  `free_space`; the following sentence names "the list itself" so its subject is not read as
  `free_space`.
- The `zfs-pool-disks` and `volume` sentences carry the narrowed rule and the 0-based naming.
- No frontmatter changed.

## RED

- [x] Text checks in the repo's own suite, run against the unchanged skill:
      `test_each_exit_code_table_names_every_cause_of_the_code[...config-deploy]` failed ("SKILL.md's row
      for 22 does not name 'config-deploy'"), and
      `test_the_skill_enumerates_the_fields_a_filesystem_really_carries` failed ("only in the payload
      ['space_is_quota']").
- [x] Text-only probe (haiku) on the old passages: a file-backed pool was answered "probably yes" for
      a `zfs-pool-disks` line; the 22 for a `--set` mode quoted NONE and cited "`--set` ... is not what
      produces this"; `space_is_quota` quoted NONE; `volume 2 on PhysicalDrive0` quoted NONE.
- [x] Contamination: the four behaviours are new in this change, so no inherited text teaches them;
      the text checks against the code are the primary evidence.

## GREEN

- [x] Same probe on the new passages quoted the governing sentence for all four questions.
- [x] Its gaps: which modes are refused was stated nowhere (closed: the `config-deploy` paragraph);
      the base of the volume count was unstated (closed: "counted from 0", with the worked example).
- [x] Declined: what value `free_space` holds on a quota row (the next sentence of the same paragraph
      defines `free_space`); whether a file-backed pool produces some other line (none does; the
      question asked only about this line).
- [x] Quote-back re-test of the two closed gaps on the revised text: `--set ...user_file=0o666` quoted
      the refusal sentence and answered 22; `--dir-mode 7_50` quoted the usage-error clause and answered
      2; `volume 0` quoted "counted from 0" and answered the first volume. Its remaining gaps declined:
      "plain octal" is shown by the examples beside it and by `--help`; a command-line mode on Windows
      is covered by "one given on the command line is still judged there".
- [x] Every test file that reads SKILL.md passes.

## REFACTOR

- [x] RED vs GREEN diffed both ways: RED produced no correct answer GREEN lost.

## Quality

- [x] No address, hostname or path from a real machine added; no session narrative.
