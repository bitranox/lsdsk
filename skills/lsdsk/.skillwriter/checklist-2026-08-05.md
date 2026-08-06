# skill-writer review: lsdsk, slots view and the mainboard-cap correction

## What changed

- `lsdsk slots` added to the command list, with a verdict table and the privilege rule.
- The "Capped by the mainboard" row and section rewritten. It previously asserted
  "No slot on this board does better", which is false whenever the board has faster
  ports that are occupied.
- The form-factor limit stated: an M.2 socket cannot be told from a PCIe card slot.

## RED

- [x] Baseline run against the pre-change text, model pinned to sonnet, answer withheld
      from the scenario. Asked whether to buy a PCIe 4.0 board for a card capped at
      Gen3 x4 on a board whose Gen4 ports are occupied.
      Result: **"Buy the PCIe 4.0 board: yes."**, justified as "follows the skill's own
      branch directly". A board purchase recommended for a machine that already has the
      hardware.
- [x] Baseline for the second arm: asked for the one command showing every slot and what
      is free. Answered `lsdsk controllers`, which reports per-controller port counts and
      cannot answer it.
- [x] A first baseline attempt passed the purchase question and was discarded as
      contaminated: the pasted tool output contained the conclusion verbatim, so the
      answer never depended on the skill. Re-run with the conclusion removed.

## GREEN

- [x] Same scenario against the new text: **"Buying a new mainboard: No."**, reasoned from
      the two-cause split rather than from anything embedded in the prompt, and correctly
      noting the scenario does not say which cause applies.
- [x] Second arm answers `lsdsk slots`, and volunteers the root requirement, quoting the
      rule that `FREE` is withheld without it.
- [x] Both dispatches required a `Skill gaps` section; both returned one.

## REFACTOR

- [x] GREEN gap closed: a `FREE` port is not automatically a safe destination, because the
      controller behind it may be oversubscribed. The two views must be read together.
- [x] RED gap closed: "swappable" was used but never defined.
- [x] RED gap closed: the privilege requirement for slot data was unstated.
- [x] RED gap closed: no way to map a PCI address to something physical. The board's own
      slot number now carries that, matched against the manual.
- [x] Declined, with reason: the skill not restating PCIe lane bandwidth (the tool prints
      GB/s directly), and the scenario not stating whether the run was elevated or what
      the finding's detail said (properties of the test scenario, not of the text; the
      skill already directs the reader to both).
- [x] GREEN diffed against RED in both directions. RED's only outputs were the wrong
      purchase recommendation and the wrong command; nothing of value is absent from GREEN.

## Quality

- [x] No session narrative, operator instructions, or scratch paths in the skill or here.
- [x] No machine-specific addresses, hostnames or paths added.
- [x] Frontmatter untouched: two fields, description remains triggers-only.
- [x] Reference skill, so the body legitimately exceeds 500 words; it stays a flat
      reference with no supporting files, so no routing table applies.
- [x] `make test` green after the change.
