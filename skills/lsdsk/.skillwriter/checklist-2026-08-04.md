# Review artifact: skills/lsdsk/SKILL.md

Skill type: reference and technique. It teaches how to run lsdsk and how to
interpret each finding class, so it is tested with application scenarios rather
than pressure scenarios.

## RED

Scenario: a real lsdsk report from a Proxmox Backup Server with five findings,
an approved hardware budget, and three forced decisions (what to buy, what is
urgent, what to do instead for everything deferred). Run without the skill,
model pinned to sonnet.

Baseline behaviour, verbatim where quoted:

- [x] Converted single readings into replacement decisions: "Replace this week;
      don't wait for a second scan to confirm a trend". Its own gaps section
      conceded this: "I treated the raw counts (78, 4, 2) as sufficient evidence
      on their own, which is weaker than the methodology the tool itself
      recommends."
- [x] Could not action "watch the count across scans": "I was only given a
      single snapshot, not a trend." It did not know the tool captures one.
- [x] Misread wear: called a drive at 59% of rated endurance "both wearing out
      and has already dropped data", and ordered a replacement for it. 59% is
      below the tool's own 80% warning threshold and is not flagged.
- [x] Asserted configuration the tool cannot see, then flagged it only afterwards
      ("I assumed the NVMe is the boot/OS drive").
- [x] Correctly declined the mainboard purchase, so that trap needed no new text.

## GREEN

Same scenario, same model, with the skill.

- [x] Snapshots before deciding: "No baseline exists yet. Take the snapshot now
      (`lsdsk snapshot -o ...`) so the next scan can show a trend instead of a
      bare number."
- [x] Wear no longer drives a replacement: it declined to attach a runway
      estimate at all, for the stated reason that rated endurance is not
      readable.
- [x] Refused the board purchase with the demand-versus-link reasoning and the
      skill's own line that the controller is already faster than the board.
- [x] Listed pool layout, bay mapping, firmware availability and pricing as
      things to confirm rather than assert.

## Diff, both directions

- [x] Checked for baseline results absent from GREEN. RED's cohort reasoning
      (drives bought together fail together) survived. RED's "replace sdp this
      week" became "order the part, snapshot, decide on the trend", which is the
      intended correction rather than a loss. No result worth keeping was lost.

## REFACTOR

- [x] Gap GREEN reported: the table treats a 4-count and a 78-count alike, and
      "the skill itself does not give a numeric second threshold". Closed, but
      not with a number: an absolute raw count is model-dependent and picking one
      would be guessing. The skill now directs the reader to the normalised value
      and the maker's own threshold, which every SMART attribute carries and
      which lsdsk already surfaces.
- [x] Remaining gaps declined with reason: all are facts lsdsk cannot read
      (pool layout, physical bay, rated endurance, whether newer firmware
      exists, prices). The skill's closing section already tells the reader to
      ask rather than infer, and GREEN followed it.

## Quality

- [x] Description states triggering conditions only, no workflow summary.
- [x] Self-contained; no supporting files, so no routing table needed.
- [x] No session narrative, no operator instructions, no scratch paths.
- [x] Hostnames and paths are the machine's own service paths and the reserved
      documentation style; no personal paths. Verified with the grep from the
      writer checklist.
- [x] Points at `lsdsk --help` rather than freezing a flag list.
