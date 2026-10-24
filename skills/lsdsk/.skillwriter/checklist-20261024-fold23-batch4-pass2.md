# skill-writer checklist - lsdsk (memory rules folded in)

- [x] Change: ## Reading `lsdsk topology` -> new paragraph after the hop-columns
      ('capable'/'running') paragraph, before 'A hop column holds a SYMBOL' - lsdsk/SKILL.md. RED on
      the pre-edit snapshot: partial. Verified: (1) The 'Graphics Port' Intel entries: zcat
      src/lsdsk/adapters/hw/decode/pci.ids.gz, filtered to vendor 8086 -> exactly 2a41 'Mobile 4
      Series Chipset PCI Express Graphics Port', a70d 'Raptor Lake PCI Express 5.0 Graphics Port
      (PEG010)', a74d 'Raptor Lake PCI Express 4.0 Graphics Port'. (2) Name column cut at '>':
      src/lsdsk/adapters/render/layout.py:35 ELLIPSIS = ">" and :508 'Fit one cell to a width,
      truncating with an ellipsis'. The skill's own sample row at SKILL.md:234 ends in 'Core i7 >'.
      (3) JSON carries the full name and address: src/lsdsk/adapters/cli/commands/scan.py:1226
      (topology --format json -> emit_json), :501 ScanData.pci_tree: tuple[PciNode, ...], :592
      pci_tree=inventory.pci_tree. src/lsdsk/domain/models.py:853-856 PciNode has address, name and
      vendor (int) but NO device id, so the device id is pointed at lspci -nn rather than claimed
      from lsdsk. (4) lsdsk cannot tell M.2 from a card slot, and slots gives the board slot number:
      SKILL.md 'What it cannot tell you, so do not assert it' (about line 1531 after the edit);
      scan.py:1337 defines the slots command. The PostToolUse table formatter re-padded the new
      table, and the cell count matches the 2-column header. The edit is ASCII only and no code span
      is split across lines.
- [x] Dropped as stale or unverified: Corrected the over-general 'Intel names its CPU root ports
      Graphics Port' (the skeptic marked it CONTRADICTED as a statement about all ports). It is now
      'Some Intel CPU root ports', followed by the exact three entries from the bundled pci.ids. The
      skeptic's amended text said to read the port's 'vendor/device id' from lsdsk; lsdsk's PciNode
      carries no device id, so that now points at `lspci -nn -s <address>` (Linux). Dropped the
      dated measurement narrative ('Measured 2026-10-09 in lsdsk: a user reported...').
- [x] Review C fixed: no pinned count, the bundled list names these Intel ports this way (exactly
      8086:2a41, a70d, a74d in the bundled pci.ids); CPU or northbridge root ports; the vendor
      prefix the tree prints; data.pci_tree; "do not change the tree, override the port's name, or
      tell the user to re-seat the drive".
- [x] Declined: listing Intel ports named "PCI Express x16 Controller": those names do not say
      "Graphics", so they do not cause the misreading this section answers.
- [x] Receipt held (the coordinator ran skill_receipt.py start meta-skill-writer); every editing
      agent loaded the skill through the Skill tool before its first edit.
- [x] RED as a text check: an opus quote-back against a pre-edit snapshot of the skill. Only a
      verbatim quote from the snapshot counts, so context inherited from this machine cannot
      satisfy it; redcheck --corpus-cascade was not run.
- [x] GREEN quote-back on haiku and opus against the edited files: every scenario after the edit,
      every scenario again after the first review's fixes, then the scenarios each later fix
      touched. Every answer was a quote from the new text on both tiers in every round; no NONE.
      Quotes were checked against the files (6-word windows; misses traced to line wraps,
      markup and line-number prefixes).
- [x] GREEN diffed against RED both ways: every partial RED quote is still in the edited text.
- [x] Tiers haiku and opus, one cell per scenario per tier per round.
- [x] Independent review of the full diff, then a review of each fix delta alone; every finding
      fixed or declined above. Each GREEN reply's Skill gaps list was read; gaps not listed
      here asked for detail the rule does not need (out of scope) or were answered elsewhere in
      the skill.
- [x] Description measured: unchanged (frontmatter not edited).
- [x] ASCII only, present tense (history finder clean on the added lines), tables canonical
      (reformat_tables.py --check --strict), every fence closes, added prose wrapped at 100
      columns except where the file keeps one line per paragraph or list item.
- [x] Security scan: prose only; no secrets, hostnames, private paths or PII; values added are
      option names, commands, source file paths or reserved examples.
