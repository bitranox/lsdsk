# Checklist - a Storage Spaces drive is read and used by its Space

## The change this documents

On Windows the reader also lists `GUID_DEVINTERFACE_HIDDEN_DISK`, where a pooled drive publishes
its interface, and links each pooled drive to its Space through the drive's removal relations
(`src/lsdsk/adapters/hw/windows/reader.py`, `usage.py`, `builder.py`; ADR 0007). A pooled drive is
in `data.disks` with a use of kind `space`; the Space carries `pool-members` only when no drive
links to it.

## Change

- The `not mounted` caveat's Storage Spaces sentences now say a pooled drive is in `data.disks`
  and read like any other drive, that its `used by` names the virtual disk it backs
  (`space:PhysicalDrive4 -> F:\`, a use of `kind` `space` whose `name` is the virtual disk's node
  and whose `mounts` are that disk's volume paths), that the virtual disk is in `data.disks` with
  bus `virtual`, and that it carries `pool-members` only when none of the pool's drives could be
  found and linked to it, `ok` then being false.

## Inherited context

- [x] `redcheck --corpus-cascade` on the lsdsk worktree reported term overlap with `CLAUDE.md`,
      `CLAUDE.local.md`, the softdev `CLAUDE.local.md` and one memory fact. A grep of each for
      `space:`, `pool-members`, the hidden disk class and Storage Spaces found no line stating
      any of the four answers (the `CLAUDE.md` hits are other uses of "hidden"; the fact names
      the probe only). The behavioural RED stands.

## RED

- [x] A text-only probe given the unchanged paragraph and an elevated three-drive Mirror scenario
      answered: the pooled drives are not in `data.disks`, quoting "is not listed at all";
      `used by` NONE; the virtual disk carries `pool-members`; `ok` is false, quoting "so such a
      scan is never `ok`".

## GREEN

- [x] The same probe on the edited paragraph: the pooled drives are in `data.disks`; each prints
      `space:PhysicalDrive4 -> F:\` with use `kind` `space`; no `pool-members` on the virtual
      disk, quoting "only when none of the pool's drives could be found and linked to it"; `ok`
      not forced false by this passage.
- [x] Every test that reads `SKILL.md`: 190 passed.
- [x] The paragraph's claims match a real elevated run on a three-drive Mirror Space: each pooled
      drive's `usage.uses` is `[{"kind": "space", "name": "PhysicalDrive4", "mounts": ["D:\\"]}]`,
      the virtual disk is in `data.disks` with bus `virtual` and no refusal
      (`tests/test_storage_spaces_disk.py` over `tests/fixtures/hw/windows-storage-spaces.json`).

## Gaps the runs reported, and their decision

- Closed: whether pooled drives are listed, their `used by` and use kind, when `pool-members`
  appears, which list the virtual disk is in, the virtual disk's own `used by`.
- Declined, excerpt effect: what makes `ok` true, which fields `readings_refused` entries carry and
  which `skipped` lines make a scan incomplete are answered by the envelope paragraph of the same
  file.
- Declined, covered by the example: several mounts are joined as every other use joins them; the
  `name` is the node the disk table prints.
- Declined, out of scope: what links a drive to its Space (instance identifiers, removal
  relations) is the reader's mechanism, recorded in ADR 0007, not something a user acts on.

## Quality

- [x] No frontmatter change.
- [x] No address, hostname or path added; `PhysicalDrive4`, `F:\` and `D:\` are generic Windows
      names.
