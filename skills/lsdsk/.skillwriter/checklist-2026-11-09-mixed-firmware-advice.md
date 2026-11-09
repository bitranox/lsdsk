# Checklist - the action for the mixed-firmware finding

## The change this documents

The "Mixed firmware" row of the findings table in `skills/lsdsk/SKILL.md` now says to ask the
vendor's tool per drive and to flash only what it offers, because batches of one model can need
different revisions and a mismatch alone is no fault. The finding's own action in
`src/lsdsk/domain/diagnostics.py` says the same.

Decisions: none - one table cell reworded; the rule's severity and threshold are unchanged.

## Ground truth

- [x] `diagnose_firmware_consistency` compares only `disk.model` and `disk.firmware`, with no
      vendor list and no network, so it cannot tell an outdated drive from a current one of
      another batch (`src/lsdsk/domain/diagnostics.py`, read in full).
- [x] The rendered finding, from `--replay tests/fixtures/hw/linux-sas-hba.json findings`, carries
      the new action.
- [x] `tests/test_diagnostics.py::test_mixed_firmware_advice_leaves_the_verdict_to_the_vendor`
      failed on the old action (`assert 'vendor' in 'Level them up. ...'`) and passes on the new
      one.

## RED (pre-edit row, text-only probe, opus and haiku, 2 cells)

Scenario: two drives of one model on different revisions, and the vendor's tool reports each as
current.

- [x] haiku: "bring the older drive (341500WD) up to the same revision as the newer one
      (341600WD)", which overrides the vendor's verdict and guesses which revision is newer.
- [x] opus: advised doing nothing, and stated that the row does not support that: "The excerpt
      does not support 'do nothing now', 'don't force a flash'".

## GREEN (new row, opus and haiku, 2 scenarios each, 4 cells)

- [x] Vendor's tool says both are current: both tiers advise no firmware change, quoting "a
      mismatch alone is no fault".
- [x] Control, the vendor's tool offers an update for one drive: both tiers apply that one update
      and leave the other drive alone, quoting "flash only what it offers". The new text does not
      turn into "never update".

## Skill gaps from RED and GREEN

- CLOSED: which verdict wins when the vendor's tool and the finding disagree; what to do on a home
  PC with no maintenance window; whether a level-up has a direction. The new row names the vendor's
  tool per drive and drops both the window and the direction.
- DECLINED, backup and power-loss precautions before a flash: generic to any firmware update and
  the job of the vendor's own instructions, not of one table row.
- DECLINED, severity not stated: an excerpt limit. The finding is `Severity.HINT` in
  `diagnose_firmware_consistency`.
- DECLINED, how to silence the finding: an excerpt limit. `SKILL.md` names
  `mixed_firmware_threshold` in its thresholds paragraph.
- DECLINED, a vendor with no tool: the row covers the common path, and advice for a tool that does
  not exist would be invented here.
- DECLINED, whether an offered update is optional or urgent: only the vendor can say that.

## Cases the edit decides beyond the reported defect

- "A mismatch alone is no fault" states what the `HINT` severity already meant. Nothing else
  changes: the finding still fires at two revisions.
