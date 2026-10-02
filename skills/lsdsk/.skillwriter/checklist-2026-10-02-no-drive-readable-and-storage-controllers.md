# Checklist - `record`'s `no drive readable` outcome, and the controller filter that no longer exists

## The changes this documents

1. `lsdsk record` on a run that could read no drive's power-on hours (every SMART
   reading refused, typically unprivileged) used to report `nothing new`, `ok`
   true, exit `0`. It now reports `outcome` `no drive readable`, `ok` false, the
   reason in `skipped` and on stderr, and exits `1`.
2. The skill taught `is_storage_controller(kind)` from `lsdsk.domain.diagnostics`
   as the controller filter. That function was deleted as a dead export; importing
   it raises ImportError.

## Change

- The `data.controllers` paragraph: `kind` lists the six values the builders can
  emit (`unknown` is dropped by both builders, so it never appears), says every
  entry is already a PCI mass-storage device so no filter is needed or exported,
  and that a USB disk's `controller_address` names a USB host controller that is
  NOT in the list.
- The "Do not hand-roll" list loses the `is_storage_controller` sentence. Every
  other function it names was imported and found present.
- The exit-code paragraph and the `1` row name `record`'s new cause; the
  `DOCUMENTED_CAUSES` guard in `tests/test_cli_exit_codes.py` gains `1`, and
  `docs/systemdesign/module_reference.md` says the same.
- The record-envelope paragraph describes the `no drive readable` run and adds the
  outcome to the enumerated list.
- No frontmatter changed.

## RED

- [x] Retrieval probe (text-only agent, weak tier, the passages as they stood).
      Q1, keep the controllers that carry disks: wrote
      `from lsdsk.domain.diagnostics import is_storage_controller`, which raises
      ImportError on the shipped package. Q2, is a USB disk's controller in
      `data.controllers`: NONE. Q3, unprivileged hourly `record --format json`:
      `ok` true, exit `0`, "the alarm on `ok == false` will not fire".
- [x] Its gaps named the USB controller's absence and that refused readings were
      nowhere tied to `record`'s envelope.

## GREEN

- [x] Same probe, new passages. Q1: no filter, quoting "Every entry is already a
      storage controller". Q2: not in the list, quoting the USB sentence. Q3: exit
      `1`, `ok` false, `no drive readable`, alarm fires.
- [x] Diffed against RED both ways: nothing RED answered correctly is lost.

## REFACTOR

- [x] GREEN gap "the sentence does not name the outcome value for this case":
      closed, the sentence now carries `outcome` `no drive readable`.
- [x] GREEN gap "no import for parsing the JSON": declined, the envelope's shape
      is documented earlier in the skill and the excerpt withheld it.
- [x] GREEN gap "is exit `1` soft or hard": declined, the paragraph says `ok`
      false means the record has stopped growing, and the sentence says it stores
      nothing until a drive can be read.
- [x] An overclaim found while writing GREEN, "stored nothing and never will",
      corrected to "will store nothing until it can read one": elevating fixes it.

## Deployment

- [x] Every test that reads SKILL.md passes, with the history and translation
      suites (13 files, 274 tests).
- [x] No address, host or path from a real machine added.
