# Checklist - a refused filesystem reading is named before the colon

## The change this documents

The skill listed `statvfs`, `zfs-pools` and `volume-space` as "subjects" of a refused reading and
omitted `volume-info`. The code (`_skipped_readings`) writes `<reading>: <subject> - <reason>`; the
reading is `statvfs`, `volume-space`, `volume-info` or `zfs-pools`, and the subject is the
filesystem's own name, or `filesystems` for the pool-level refusal. The TUI line also said
"r rescans, q quits" where the bindings are `r,f10` and `q,escape`.

## Change

- Refused-reading sentence: states the line shape and which word is the reading and which the subject.
- TUI line: "r or f10 rescans, q or escape quits".
- No frontmatter changed; no body line outside these two passages.

## RED

- [x] Text check: the old sentence calls the reading names "subjects" and lacks `volume-info`;
      `grep -n volume-info skills/lsdsk/SKILL.md` found nothing, and
      `src/lsdsk/adapters/hw/windows/filesystems.py` line 52 produces it.
- [x] Contamination: a text check of the artifact against the code, immune to inherited context.

## GREEN

- [x] Every test file that reads SKILL.md, COMMANDS.md, CHANGELOG or the ADR passes, plus
      test_translations.py and the full suite.

## REFACTOR

- [x] No gaps reported; no probe dispatched, since the edit states what the code produces.

## Quality

- [x] No address, hostname or path from a real machine added; no session narrative.
