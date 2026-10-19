# Checklist - `kind` gains `optical`; a Storage Spaces drive is not listed

## The change this documents

A Linux CD, DVD or Blu-ray drive is kind `optical`, decided from SCSI peripheral device type 5
(`DiskKind.OPTICAL`, `src/lsdsk/domain/enums.py`; `src/lsdsk/adapters/hw/linux/builder.py`).
A drive in a Windows Storage Spaces pool is not in the disk list, and the pool's virtual disk
carries a `pool-members` refusal (`src/lsdsk/adapters/hw/windows/builder.py`).

## Change

- The disk paragraph's `kind` list reads `ssd`, `hdd`, `optical` or `unknown`, and its never-null
  list gains `kind` and `link`, which the `Disk` model never leaves `null`.
- The `not mounted` caveat no longer names a Storage Spaces member: a pooled drive is not listed at
  all, and the pool's virtual disk carries `pool-members`, so such a scan is never `ok`.
- `tests/test_the_skill_agrees_with_the_package.py` gains
  `test_the_skill_names_exactly_the_bus_and_kind_values_a_disk_can_carry`, which parses the
  sentence and compares both sets with `BusType` and `DiskKind`. It failed on the unchanged
  sentence (`the skill names kinds ['hdd', 'ssd', 'unknown']`) and passes on the new one.

## Inherited context

- [x] `redcheck --corpus-cascade` on the lsdsk directory reported term overlap with `CLAUDE.md`,
      `CLAUDE.local.md` and one memory fact. A grep of each for `optical`, `pool-members` and
      Storage Spaces found one line (`CLAUDE.md`, a `sr` device being once misread as virtual),
      which states neither answer. The behavioural RED stands.

## RED

- [x] A text-only probe given the unchanged paragraph listed `"ssd"`, `"hdd"`, `"unknown"` and
      `null` for `kind`, answered NONE for a DVD drive, and said the pooled drives "can show
      `not mounted`", quoting "a Storage Spaces member or an exported ZFS pool shows it too".

## GREEN

- [x] The same probe on the edited paragraph: four strings, no `null` branch, quoting "`kind` is
      `ssd`, `hdd`, `optical` (a CD, DVD or Blu-ray drive, which only Linux lists) or `unknown`."
      and the never-null list; a DVD drive is `"optical"`; the pooled drives have no row, the
      virtual disk carries `pool-members` and `ok` is false, quoting the new Storage Spaces
      sentence.
- [x] Every test that reads `SKILL.md`: 190 passed.

## Gaps the runs reported, and their decision

- Closed: no optical kind; `kind` null versus `unknown`; Storage Spaces drives showing
  `not mounted`.
- Declined, excerpt effect: what makes a scan complete, and when a disk counts as not read, are
  answered by the envelope paragraph and the root/Administrator paragraph of the same file.
- Declined, covered: the literal `skipped` line for `pool-members` follows the general
  `<reading>: <subject> - <reason>` rule the envelope paragraph states.
- Declined, unmeasured: whether a drive is hidden from the moment it joins a pool or only once a
  virtual disk exists on it; only the second state was measured.
- Declined, out of scope: the virtual disk's own `bus`/`kind` cells, and whether the `kind` set
  may grow (the new test now pins the sentence to the enum).

## Quality

- [x] No frontmatter change.
- [x] No address, hostname or path added.
