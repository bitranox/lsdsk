# Checklist - hints for the PCIe links no storage rule grades

## The change this documents

lsdsk grades every PCIe link no storage rule grades and raises a HINT where a card
runs on fewer lanes than both ends support, or where its slot caps it below what
the card can do, naming a free slot that would carry more where the connector bits
were read. A speed-only shortfall is deliberately not reported. A bridge or switch
at the card end is named by what it carries.

## Change

- Two rows in the "Reading a finding" table, each prefixed "Any other PCIe card:"
  so they cannot be read as the storage rows they sit beside.
- One paragraph after the faster-slot one: hint severity and exit code 0, not a
  storage finding, how the title names a switch or bridge, why a speed-only
  shortfall is never reported, what the "was not readable" action means, what the
  action says when no slot would help, and that Windows raises neither hint.
- No frontmatter, no field list, no quoted output block, no link figure.

## RED

- [x] `redcheck --corpus-cascade` on the worktree reported STRONG inherited
      coverage from the lsdsk CLAUDE.md, on shared vocabulary only (card, capped,
      gen3x16, link): no document in the cascade mentions non-storage link hints,
      the carrying clause or idle downtraining (checked by grep), so the
      behavioural arm below is the evidence.
- [x] Retrieval probe (text-only agent, the section as it stood, three real
      findings from the committed captures, every answer a direct quote or NONE):
      it filed the switch finding under "Capped by the mainboard", the storage row;
      quoted NONE for why a dual-GPU card is named as its switch; could not say
      whether the hints change the exit code; stretched the storage slot-grading
      sentence to explain an idle graphics card; had no text for the
      "was not readable" action; and gave the bridge card the storage advice
      "try another bay, before suspecting the drive".

## GREEN

- [x] Same probe on the new section: all six questions answered by a direct quote
      of the new rows or paragraph, including the switch naming ("The title names
      the device at the card end of the link ..."), the idle case ("A link that only
      runs SLOWER than both ends support is never reported ..."), and the re-run as
      root for an unread connector.
- [x] Every test that reads SKILL.md, found by grepping `tests/` for the filename:
      144 passed.

## REFACTOR

- [x] GREEN diffed against RED both ways: the one RED answer that was right (the
      lanes-lost detail points at a contact) survives in GREEN, now from the
      card's own row; nothing RED produced is missing.
- [x] Gaps declined: the general hint-to-exit-code rule is stated in the skill's
      exit-code section, outside the excerpt the probe was given; a bare graphics
      card with no switch is covered by "Any other PCIe card", whose paragraph
      names a graphics card first; the two hints are answered row by row, which is
      how the table is meant to be read.

## Deployment

- [x] No address, host or path from a real machine in the skill text.
- [x] No typographic tells; table realigned by the repository's table formatter.
