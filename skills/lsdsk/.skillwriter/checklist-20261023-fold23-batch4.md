# skill-writer checklist - lsdsk (fold of memory rules into the skill)

- [x] Change: Privilege section, new paragraph directly after the five-row 'Needs privilege' table
      and its 'The last one is refused on some hosts even as root' note, before '**The interactive
      view asks for elevation itself.**'. Verified against ground truth: Windows builder sets only
      negotiated_gbps and drive_max_gbps from the IDENTIFY identity, no port_max_gbps ->
      src/lsdsk/adapters/hw/windows/builder.py:483-488 (comment 'The reader records no port
      capability for a disk, so the port end of the link stays unmeasured'); port_max_gbps filled
      only by the Linux builder -> grep port_max_gbps src/lsdsk shows the only producer
      src/lsdsk/adapters/hw/linux/builder.py:778 (_sata_link); machine panel row ->
      src/lsdsk/adapters/render/detail.py:407 ('privileged', _yes_no(...)) inside the MACHINE group;
      refusal reason text -> src/lsdsk/adapters/hw/windows/reader.py:71 _NEEDS_ADMINISTRATOR =
      'needs Administrator to open the device for passthrough', set as ata.
- [x] Changed after review: the Windows SATA `port` column is never read directly; unelevated it
      reads `-`, an elevated run can show the `>=` floor of the drive's own maximum.
- [x] Claims the source contradicts were dropped or corrected; machine-local figures, private names
      and incident history were not carried over.
- [x] Receipt held (the coordinator ran skill_receipt.py start meta-skill-writer); every editing
      agent loaded the skill through the Skill tool before its first edit.
- [x] RED as a text check: an opus quote-back against the pre-edit snapshot (partial for scenario
      1). Only a verbatim quote from the snapshot counts, so context inherited from this machine
      cannot satisfy it; redcheck --corpus-cascade was not run.
- [x] GREEN quote-back on haiku and opus against the edited files, in four rounds: after the edit,
      after the first review's fixes, after the delta review's fixes, and a final round on the
      questions the last fixes touched. Every scenario was answered with a quote from the new text
      on both tiers in every round; no NONE.
- [x] GREEN diffed against RED both ways: every partial RED quote is still in the edited text;
      nothing the baseline answered was lost.
- [x] Tiers haiku and opus, one cell per scenario per tier per round.
- [x] Independent review of the full diff, then a review of each fix delta alone; every finding
      fixed or recorded below.
- [x] Gaps closed or declined: closed - the port-floor wording (re-tested by quote-back on haiku and
      opus); declined - an elevated run that is still blank (a driver or hardware refusal with no
      lsdsk-side signal to document), how to obtain an elevated shell (the skill's existing
      elevation section), a pasted report missing the panel lines (ask for the full report).
- [x] Description measured: unchanged (frontmatter not edited).
- [x] Security scan: prose only; no secrets, hostnames, private paths or PII; values added are
      option names, commands, repo file paths or reserved examples.
