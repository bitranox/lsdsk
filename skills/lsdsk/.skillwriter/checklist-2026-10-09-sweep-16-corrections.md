# Checklist - sweep-16 corrections to the skill

## The change this documents

Six statements in the skill no longer matched the tool:

- the `data.trend` counter list named six counters; the trend watches seven (`error_log_entries`);
- "no network" was stated without exception, while an opt-in Graylog log sink can connect;
- the exit-code table had no row for `130`, which an interrupted run now leaves;
- nothing said what `config-deploy` / `config-generate-examples` answer when run twice
  (`ok` true, exit `0`, `data.outcome` `already present`);
- nothing said how an empty `data.trend` from an unreadable history store differs from one with
  nothing to show (`ok` false plus a `skipped` sentence);
- the `13` row did not cover a configuration file the run may not read (`PERMISSION_DENIED`).

## Change

- Opening paragraph, "lsdsk does not go online" and the limits paragraph: the network wording
  names the Graylog sink (`lib_log_rich.enable_graylog`, off by default) and says it fetches
  nothing.
- Exit-code table: the `13` row gains the unreadable configuration file; a `130` row is added.
- The paragraph on the `config` commands states the re-run shape.
- The trend JSON paragraph names `error_log_entries` (NVMe drives only) and `percent_used` with
  their table labels and says `kind` equals `counter`; a new paragraph explains reading `ok`
  beside an empty `data.trend`, quoting the `skipped` sentence the tool prints.
- No frontmatter changed.

## RED

- [x] Text check: `test_the_trend_json_paragraph_names_every_counter_the_trend_watches` (reads
      `WATCHED`, not a list) failed on the skill with `['error_log_entries']` and passed on
      COMMANDS.md, which proves it can pass.
- [x] Exit-code check: `test_every_code_the_tool_can_raise_is_documented_where_a_caller_reads`
      exempted 130 as "never raised here", which is no longer true; the exemption now holds 143
      only, and the guard requires the `130` row.
- [x] Retrieval probe (text-only agent, weak tier, old excerpts), six questions. It guessed
      `media_errors` for the NVMe error log; found NONE for exit 130; NONE for the config-deploy
      re-run shape; told the caller to parse stderr to read an empty `data.trend`; guessed `78`
      / `CONFIG_ERROR` for an unreadable config. On the network question it answered "unclear"
      only by citing context outside the excerpt, so that question is evidenced by the text
      change itself, not by this arm.

## GREEN

- [x] Same probe, new excerpts: `error_log_entries`; "yes, only when the Graylog sink is
      enabled"; 130 is an interrupt, re-run, do not page; `ok` true, exit `0`, branch on
      `data.outcome`; read `ok` and `skipped` beside an empty list; exit `13`,
      `PERMISSION_DENIED`. Each answer quoted the new text.
- [x] Diffed against RED both ways: every RED guess is replaced by a quoted answer; nothing RED
      answered correctly was lost.

## REFACTOR

- [x] GREEN gaps closed: which key enables the sink (named), whether a SATA drive can carry
      `error_log_entries` (NVMe drives only). Quote-back probe returned a direct quote for both.
- [x] Declined: telling "nothing worth a line" from "nothing recorded yet" with `ok` true - the
      envelope carries no signal for it, and the paragraph says the list means either.
- [x] Declined: the error object's shape and the `ok`-versus-exit-code split - both are stated
      elsewhere in the skill; the probe saw an excerpt.

## Deployment

- [x] Every test that reads SKILL.md or COMMANDS.md passes, plus the exit-code suite.
- [x] No address, host or path from a real machine added.
- [x] Plugin version bumped with the change, since 1.7.1 is already consumed.
