# skill-writer review: lsdsk, the one-page report as the default

## What changed

- The overview now states that a bare `lsdsk` renders the whole machine, and that
  every subcommand is one section of it.
- The command list gains `lsdsk topology` (the former `scan`) and drops the separate
  one-page command, which the default replaced.
- The overview states that lsdsk names the mainboard, from DMI.

## RED

- [x] Baseline against the pre-change text, model pinned to sonnet. Asked for the SINGLE
      command producing a handover report covering mainboard, controllers, per-disk wear
      and error counters, PCIe slots and faults.
      Result: chained five commands, and stated plainly that "no single `lsdsk`
      subcommand in the skill text produces the mainboard, controllers, every disk with
      wear/error counters, PCIe slots, and problems all in one report."
- [x] The same run reported a second gap unprompted: "No mention of 'mainboard' as a
      concept at all ... the skill gives no evidence lsdsk reads DMI/SMBIOS at all."

## GREEN

- [x] Same scenario against the new text: answered with the one-page command directly,
      named the sections it covers, and added the root caveat for the wear counters.
- [x] Both dispatches required a `Skill gaps` section; both returned one.

## REFACTOR

- [x] GREEN gap closed: the text did not say whether the one-page report degrades or
      aborts without root. It now states that every section still renders, unread values
      show `-`, and the header names them.
- [x] Declined, with reason: the slot/connector privilege rule (already stated in the
      skill's own slots section, absent only from the trimmed excerpt the test saw), uvx
      reachability on an air-gapped host (not this skill's subject), and a sample of the
      output (the README carries one; the skill body stays lean).
- [x] GREEN diffed against RED in both directions. RED's only output was the five-command
      chain the change exists to replace; nothing of value is absent from GREEN.
- [x] Re-checked after the surface was reworked so the report became the DEFAULT rather
      than a named command: the skill text, the command list and this record all describe
      the shipped surface, with no reference to a command that no longer exists.

## Quality

- [x] No session narrative, operator instructions, or scratch paths in the skill or here.
- [x] No machine-specific addresses, hostnames or paths added.
- [x] Frontmatter untouched: two fields, description remains triggers-only.
- [x] `make test` green after the change.
