# Checklist - two run-wide filesystem refusals, and what a refusal does to `ok` and the rows

## The change this documents

The code writes two run-wide refusals the skill did not list: `zfs-pool-disks`
(`src/lsdsk/adapters/hw/linux/filesystems.py`, a pool the ZFS listing names that no inventoried
disk can be traced to; reason `pool <name>: no member was traced to a disk this scan inventoried`)
and `volume` (`src/lsdsk/adapters/hw/windows/filesystems.py`, a Windows volume whose paths or disk
extents could not be read). Neither draws a row.

## Change

- The refused-reading paragraph lists both readings with the subject `filesystems`, says what each
  means, that such a pool or volume has no row and its fill is not judged, and that the `volume`
  reason names the volume by its drive letter where it has one.
- Its opening sentence now says a `skipped` line makes `ok` false (`scan.py`: `ok=not skipped`) and
  that a row whose reading was refused stays in the list with that figure `null`. The old wording,
  "not as a missing row", contradicted the new no-row readings.
- No frontmatter changed; no body line outside this paragraph.

## RED

- [x] Text check: `grep -c zfs-pool-disks skills/lsdsk/SKILL.md` was 0, and `volume` appeared only
      as a `kind` value, never as a reading.
- [x] Probe (text-only agent, sonnet) given the old paragraph and a run whose `skipped` holds a
      `zfs-pool-disks` and a `volume` line: the quote for each line's meaning and for whether the
      pool's fill is judged was NONE.
- [x] Contamination: the readings are new in this change, so no inherited text teaches them; the
      text check against the code is the primary evidence.

## GREEN

- [x] Same probe on the new paragraph quoted the governing sentence for each line's meaning and for
      "fill is not judged".
- [x] Its gaps: the opening "not as a missing row" contradicted the no-row sentence; `ok` unstated;
      the `volume` reason's naming unstated. All three closed in the text (see Change).
- [x] Re-test on the revised paragraph: quoted the governing sentence for why `ok` is false,
      whether the pool's fill is judged, and which volume the line concerns; found no
      contradiction. Its one wording gap ("Neither" with two antecedents) closed by naming them.
- [x] Every test file that reads SKILL.md passes in the full suite.

## REFACTOR

- [x] Declined: remediation advice for an untraced pool or a not-ready volume (out of scope: the
      reason text carries the cause, and the skill describes output, not repair); interpreting the
      Windows error text (passed through from the system verbatim).
- [x] RED vs GREEN diffed both ways: RED produced no correct result the GREEN runs lost.

## Quality

- [x] No address, hostname or path from a real machine added; no session narrative.
