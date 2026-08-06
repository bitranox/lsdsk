# skill-writer review: two claims the code contradicts

## What changed

- The privileges paragraph said only SMART and wear need root. Four things do, and the
  other three were stated elsewhere in the file or nowhere: error counters (so `trend` and
  `record`), PCIe slot numbers and connector detection, and the AHCI ports-implemented
  bitmap. They are now one table, with what each costs when it is missing.
- The AHCI row carries the caveat that it is refused on some hosts even as root, so a `-`
  there does not prove the run was unprivileged.
- Exit codes became a table covering `13` and `22`, which existed and were undocumented.
- `2` is stated as the CLI framework's usage error with all five causes, replacing "a
  missing file", and with the misread named because that is the failure it produces.
- Option placement: `--replay` and `--profile` work in either position, `--history-file`
  and `--no-record` only before the command.

## RED

- [x] Exit codes. Asked what a cron wrapper should do about exit `2` from
      `lsdsk health --replay ... --format json`. The agent answered "the capture file is
      missing", then told the wrapper to "page whoever owns the capture step, not the
      disk-health on-call". The same code is produced by a typo in the wrapper's own
      command line, so the recommended action pages a team that cannot fix it.
- [x] Privileges, first probe, PASSED and was not treated as a verdict. Asked directly
      whether an unprivileged user gets free-slot data, the agent found the correct later
      sentence and answered correctly. A capable model reconciling a contradiction is not
      evidence the contradiction is harmless.
- [x] Privileges, re-probed in the shape the failure takes: write a COMPLETE "do I need
      root" runbook section. The agent had to quote five scattered sentences to assemble
      the list, and the first one it quoted was the false one, contradicted by the rest of
      its own answer. It reported unprompted that it could not tell whether the AHCI
      free-port count needs root, and left it out rather than guess.

## GREEN

- [x] Exit codes: the agent now leads with "the command line itself was rejected before
      lsdsk ever tried to open host7.json", names the misread explicitly, and routes the
      wrapper to stderr before alerting.
- [x] Privileges: the complete four-item list assembled from one table, with both caveats
      (a container where root does not help, a blind run that is not a clean bill of
      health) carried into the runbook section unprompted.
- [x] Diffed both directions. Nothing the baselines produced is missing from GREEN: the
      `78`-versus-`2` distinction and the slot-move preconditions both survive.

## REFACTOR

- [x] GREEN reported that the file never says whether `--replay` is global or
      per-subcommand, and that the only example shows it with no command at all. Closed.
      This is the same flag whose resolution was defective in the code, so a reader had no
      way to know which invocation was even valid.
- [x] The first draft of that fix asserted `--history-file`, `--no-record` and `--profile`
      behave like `--replay`. Checked against the running tool before shipping: false.
      `--history-file` and `--no-record` exist only before the command, and
      `lsdsk record --history-file h.json` is itself a usage error exiting `2`. Rewritten
      to what the tool does, and every clause in the paragraph verified by running it.
- [x] Declined, with reasons: no example stderr text (the message is not a stable contract
      and `--help` is the current source); the same requirement appearing in three
      sections (each states its own local consequence, and merging them would move the
      privilege note away from the finding it explains); no minimum-capability guidance
      such as `CAP_SYS_RAWIO` (unmeasured, and a claim about it would be the kind of
      untested assertion this review exists to remove).

## Verification

- [x] Every claim added was checked against the running tool or the source, not inferred:
      both `--replay` positions produce byte-identical output, the subcommand's value wins
      when both are given, `--profile` behaves the same on `config`, and
      `record --history-file` exits `2`.
- [x] The four root requirements traced to code: the passthrough ioctls, the PCIe
      capability walk past the first 64 bytes of config space, and the `mmap` of BAR5.
- [x] `src/lsdsk/adapters/hw/linux/reader.py` carried the same omission in its own module
      docstring, listing one root-gated read where there are three. Fixed in the same pass.
- [x] No session narrative, no machine-specific addresses or paths added.

## Follow-up in the same session

- [x] An adversarial review of the shipped commit found the sentence "only `tui` has none"
      still describing the structured mode, contradicting the module reference written in
      the same change. `fail` and `logdemo` have none either. Corrected in SKILL.md, in
      CHANGELOG.md, and confirmed against `lsdsk <command> --help` for all seventeen
      commands: fourteen offer `--format`, three do not.
- [x] A later pass found two more claims in the same file that the code contradicts, both
      fixed here: exit `22` had gained a second cause (a `--profile` name the configuration
      library rejects, verified by running `config-deploy --target user --profile 'bad
      profile!!'`, which exits 22), and the counter store was described as "bounded in
      size" when the cap is per drive and a removed drive keeps its series. Both corrected
      against the running tool and the source, not against the previous wording.
