# Checklist - sweep-17 corrections to the skill

## The change this documents

Sweep 17 made five statements in the skill untrue or incomplete:

- the quoted topology rows showed a SAS-attached drive's port as `12G`, a figure borrowed from a
  neighbouring phy; the tool now draws that end as unread (`-`) when the capture records no
  phy-to-port link;
- the SAS port-count paragraph said a 9500-16i publishes twenty-one phys; lsdsk now counts the
  controller's own host phys that report a link rate (16), never an expander's;
- the refused-history sentence was described for `trend` only; every command judged against the
  history now reports it, with its own consequence clause;
- two new `skipped` lines were undocumented: Windows' `device: disk interface - <reason>` and
  plain `config`'s `Ignored: ...` line;
- the list of rules "callable on its own" omitted `diagnose_usb_link`, and six options were named
  nowhere in the skill (`--permissions`, `--no-permissions`, `--dir-mode`, `--file-mode`,
  `--theme`, `--no-traceback`).

## Change

- Quoted topology row: port column `-`.
- SAS paragraph: host phys with a link rate, 16 on the 9500-16i, lanes rather than sockets.
- Trend paragraph followed by one naming the other seven commands and quoting their sentence.
- Envelope paragraph: the Windows disk-interface entry, that it is not a disk in `data`, and the
  header line. Config paragraph: the `Ignored:` line, `ok` false at exit `0`.
- Rule list gains `diagnose_usb_link(disk)` and says each rule returns a `list[Finding]`; the
  options paragraph names the `config-deploy` permission options with their defaults, the global
  `--no-traceback`, and `logdemo --theme`.
- No frontmatter changed.

## RED

- [x] Text checks written before the edit, each failing on the old skill:
      `test_the_skill_names_every_rule_diagnostics_exports` (`['diagnose_usb_link']`),
      `test_the_skill_names_every_option_the_cli_offers` (the six options above),
      `test_the_skill_quotes_the_refused_history_sentence_each_command_gives[DISKS]` (its TREND
      arm passed, which proves the instrument can pass), and
      `test_the_skill_states_the_sas_port_count_the_sample_capture_gives` (reads the 9500-16i's
      count from the `linux-sas-hba` capture rather than a written figure).
- [x] `test_a_quoted_block_is_output_the_tool_really_produces[skills/lsdsk/SKILL.md:...]` failed on
      the quoted `12G` row.
- [x] The SAS count is held by the capture test, not a behavioural arm: this repository's
      CLAUDE.md, which a dispatched subagent inherits, states the 16-port figure, so a probe could
      not fail honestly on it.
- [x] Retrieval probe (text-only agent, weak tier, old excerpts), five questions: NONE for the
      refused-history sentence on `health`; NONE for whether a disk interface is a disk; NONE plus
      a wrong guess ("a history setting was turned off") for `Ignored: history=false`; named the
      wrong rule (`diagnose_disk_link`) for a USB link; NONE for file modes and the theme option.

## GREEN

- [x] All four text checks and the quoted-output check pass; every test that reads SKILL.md
      passes.
- [x] Same probe, new excerpts: each answer quoted the new text - the `its verdicts were judged
      without it` sentence; not counted in `data`, header line; section given as a single value,
      shipped section used, nothing failed; `diagnose_usb_link(disk)`; `--file-mode 640`,
      `--no-permissions`, `logdemo --theme`.
- [x] Diffed against RED both ways: every RED NONE or wrong guess is replaced by a quoted answer;
      RED answered nothing correctly that GREEN lost.
- [x] The umask claim was run, not inferred: under umask 027, `--no-permissions` wrote 640/750 and
      the default wrote 600/700.

## REFACTOR

- [x] Declined: the theme names and whether `--file-mode` works alone - `lsdsk <command> --help`
      lists both, and the skill points there.
- [x] Closed: the GREEN probe could not name `diagnose_usb_link`'s return type, and the skill
      stated no rule's. Every `diagnose_*` in `diagnostics.__all__` is annotated `list[Finding]`
      (read from the signatures), and the rule list now says so.
- [x] Quote-back for the return type: "Each returns a `list[Finding]`, empty when the rule has
      nothing to say."
- [x] Declined from the quote-back's gaps: what a `Finding` holds (the skill's findings section
      states it; the probe saw one paragraph), and a `Thresholds` argument for
      `diagnose_usb_link` (its signature takes the disk alone, as written).
