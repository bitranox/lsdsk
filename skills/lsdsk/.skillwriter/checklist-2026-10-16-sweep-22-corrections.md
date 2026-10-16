# Checklist - disk-interface lists, volume enumeration, zvol pools, a forced deploy, the trend verdict, the temperature limit

## The change this documents

Seven behaviours of the code the skill described wrongly or not at all:

- A Windows disk-interface list cut short is no longer counted in `N disk interface(s) not read`; the
  note adds `Not every disk interface was listed: <reason>.` (`src/lsdsk/adapters/render/report.py`).
- A Windows volume list that could not be enumerated, or ended part-way, is a `volume` refusal and
  makes the run incomplete (`src/lsdsk/adapters/hw/windows/volumes.py`).
- A Windows volume whose paths alone failed, with extents naming only disks the scan does not list,
  draws no `volume` line.
- A ZFS pool on partitions of a zvol is on no disk, with no row and no `zfs-pool-disks` line.
- `config-deploy --force` skips a malformed file it is about to rewrite, warns once naming it and
  replaces it, instead of exiting 78.
- The trend verdict for a below-one expectation reads `too soon to say, fewer than 1 was due`
  (`src/lsdsk/adapters/render/trend.py`).
- The temperature finding fires at or above the limit, and on Windows the limit can come from the
  storage stack where SMART names none (`src/lsdsk/domain/diagnostics.py`,
  `src/lsdsk/adapters/hw/windows/builder.py`).

## Change

- The disk-interface paragraph: reasons joined by `; `; the cut-short list has its own sentence and
  is not counted.
- The `zfs-pool-disks` / `volume` paragraph: zvol partitions named; the paths-only exception; a new
  sentence for a volume list that could not be enumerated or ended part-way.
- The exit-78 row: the `config-deploy --force` exception, ending 0 when nothing else fails.
- The quoted trend row and the "Read the refusals as refusals" prose: the new wording, as a bound.
- The temperature row: "At or above", with the Windows storage-stack source.
- Tables re-padded by the formatter. No frontmatter changed.

## RED

- [x] Repo text check against the unchanged skill:
      `test_a_quoted_block_is_output_the_tool_really_produces[skills/lsdsk/SKILL.md:device        counter]`
      failed (the quoted trend row is no longer what the tool prints).
- [x] Text-only probe (opus, inert probe type) on the old passages, seven questions: the cut-short list
      was answered "counts 2 ... `2 disk interface(s) not read`"; the failed enumeration NONE; the
      paths-only volume "Taken literally, yes"; zvol partitions "Not answerable for certain"; the forced
      deploy "Probably 78"; the verdict quoted the old sentence; 70 C at a 70 C limit "Most likely no
      finding" and the Windows source NONE.
- [x] Contamination: the seven behaviours changed in this change, so no inherited text teaches the new
      answers; the repo's quoted-output test is the mechanical evidence.

## GREEN

- [x] Same probe on the new passages quoted the governing sentence and answered all seven correctly.
- [x] Its gaps: the exit code of the forced deploy was unstated (closed: "exits `0` when nothing else
      fails"); the finding's name said "Above" while its meaning said "At or above" (closed: the name
      reads "At or above").
- [x] Declined: the value of E and which pages carry the note (placeholders and "every human page
      that lists disks" are the contract); that the line lands in `skipped` (the surrounding section,
      outside the excerpt, is the `skipped` section); warning-versus-critical severity (not what the
      row documents).
- [x] Every test file that reads SKILL.md passes.

## REFACTOR

- [x] RED vs GREEN diffed both ways: RED produced no correct answer GREEN lost.

## Quality

- [x] No address, hostname or path from a real machine added; no session narrative.
