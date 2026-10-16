# Checklist - Windows device lists that SetupAPI cut short

## The change this documents

The Windows reader records a device list that SetupAPI ended with any error other than
`ERROR_NO_MORE_ITEMS` (`src/lsdsk/adapters/hw/windows/reader.py`, `_note_list_end`), and the
builder turns it into a refused reading (`build_unread_interfaces`, `build_unread_device_lists` in
`src/lsdsk/adapters/hw/windows/builder.py`):

- a cut-short disk-interface list is one more `device: disk interface - <reason>` entry, its reason
  `the list of disk interfaces ended early at entry N (Win32 error E), so whatever followed was not
  read`, counted as one in the human page's `N disk interface(s) not read` note
  (`report.unread_interfaces_note`);
- any other cut-short list is `<list>: device list - <reason>`, `<list>` being `PCI devices`,
  `USB devices` (`_present_devices` enumerators) or `USB hubs` (`_INTERFACE_LISTS`), and it reaches
  `skipped` only (`scan.py`, the `device list` subject); no human page prints it.

`N` is the 1-based number of the entry whose enumeration call failed, so entries from N on were not
read.

## Change

- The `skipped` paragraph's disk-interface sentence is followed by the cut-short disk-interface
  case and the `<list>: device list` form, naming the three lists and what goes missing.
- No frontmatter changed.

## RED

- [x] Text-only probe (haiku, inert probe type) on the old paragraph: "which other device lists can
      appear this way" quoted NONE; a `PCI devices: device list` entry was "not classified" by the
      passage; whether a cut-short disk-interface entry means one drive or several was "unknown".
- [x] Contamination: the behaviour is new in this release and no CLAUDE.md or memory text teaches
      it; the forms were checked against the source and against
      `tests/test_windows_truncated_enumeration.py`, which asserts the exact `skipped` lines.

## GREEN

- [x] Same probe on the new paragraph quoted the governing sentence for all three questions and
      answered them correctly: a controller can be missing, the PCI entry is not on a human page, the
      `1` in the note does not mean one drive, and the three lists by name.
- [x] Its gaps: whether entry N itself was read (closed: "from entry N on", matching the reader's
      1-based count of the failing call; quote-back re-tested). Declined: what a Win32 error number
      means (Windows' own code, passed through verbatim); whether such an entry turns `ok` false
      (the paragraph's opening sentence on `ok` and `skipped` governs it); whether the USB lists
      reach a human page ("in `skipped` only" covers every `<list>`); whether "a controller" includes
      storage controllers (it names any controller, by design).
- [x] Every test file that reads SKILL.md passes (175 tests).

- [x] Quote-back on the closed gap: "was the drive at entry 4 read" quoted "every drive from entry N
      on is missing from `data`" and answered no. Its one gap, declined: the tool's own reason text
      says "whatever followed was not read", which on its face excludes entry N; the skill quotes
      that text verbatim, and the sentence after it states the inclusive rule.

## REFACTOR

- [x] RED vs GREEN diffed both ways: RED produced no correct answer GREEN lost.
- [x] No address, hostname or private path added.
