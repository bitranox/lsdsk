# Checklist - what a snapshot names, and what `not mounted` does not mean

## The changes this documents

1. A snapshot now records where each disk is used: block-backed mountpoints, drive letters, and
   the pools, volume groups and arrays a disk belongs to.
2. `not mounted` in the `used by` column does not mean a disk is safe to wipe: a disk passed
   through to a virtual machine, a Storage Spaces member or an exported ZFS pool shows it too.

## Change

- The ticket paragraph says a snapshot names where each disk is used, beside the machine and its
  drives.
- The `usage` field paragraph gains one sentence: `not mounted` is not "safe to wipe", with the
  three cases that show it.
- No frontmatter changed.

## RED

- [x] Quote-back retrieval probe (text-only agent, weak tier, the two passages as they stood). Q1
      "does a snapshot reveal where my disks are mounted?": quoted "A snapshot names the machine
      and every drive in it", and its own gaps list said the passage does not confirm mount paths
      or pool names are included - the text does not answer. Q2 "`not mounted`, can I wipe it?":
      NONE.

## GREEN

- [x] Same probe, new passages. Q1 quoted "A snapshot names the machine, every drive in it and
      where each disk is used: its mountpoints or drive letters and the pools, volume groups and
      arrays it belongs to." Q2 quoted "`not mounted` does not mean the disk is safe to wipe: ...".
      Skill gaps: none reported.
- [x] Diffed against RED both ways: nothing the RED answered is lost; both answers moved from
      non-answer or NONE to a direct quote.

## REFACTOR

- [x] No GREEN gaps to close.

## Deployment

- [x] Every test that reads SKILL.md passes (160, with the translation gate).
- [x] No address, host or path from a real machine added.
- [x] No plugin version bump: the release step owns it.
