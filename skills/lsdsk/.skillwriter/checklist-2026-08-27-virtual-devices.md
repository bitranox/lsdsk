# skill-writer review: devices with no hardware behind them

1.1.0 makes lsdsk read kernel-virtual block devices instead of dropping them,
folds them into a tally, and adds `--expand-virtual`, `display.expand_virtual`
and `data.virtual_disks`. Two statements in the shipped skill were made false by
it, and the RED run found a third gap that predates the change.

## RED

Two tasks, given to an isolated agent with the pre-change text: show a Proxmox
host's zram and zvols and make that the default, and write a CI check that fails
if a device with no transport appears among the real drives.

It answered "THE SKILL DOES NOT SAY" to both, and named the reason:

> No documented config key controls which device types/transports lsdsk
> inventories; the only config surface described is thresholds, history, and
> deploy/display mechanics.

> No JSON schema for a per-disk row (from `disks` or `snapshot`) is given
> anywhere in the skill - no field names, no key path, no mention of a
> transport/bus/kind attribute.

It declined to guess `data.disks[].bus`, which is the correct field, rather than
infer it - so the gap was the text's, not the reader's.

## GREEN

Same two tasks, new text. Both answered with a command and a config path, each
quoting the skill, and the run picked up the platform caveat unprompted.

## Gaps reported by the test runs

| Gap                                                                        | Outcome                                                                           |
|----------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| No per-disk JSON row schema anywhere (RED)                                 | CLOSED. The thirteen fields are named, with which may be null and what null means |
| No documented lever for which devices are inventoried (RED)                | CLOSED. The new section, with the flag and the key                                |
| `snapshot -o` shape assumed to match the envelope (RED)                    | CLOSED. Stated as the raw reading, not a list of disks                            |
| Windows caveat raised with no corrected check (GREEN, opened by this edit) | CLOSED. A disjoint-lists check that holds on both platforms                       |
| `config-deploy --target app` and `host` paths unstated (GREEN)             | DECLINED. Pre-existing, unrelated to this change, and owned by CONFIG.md's        |
|                                                                            | platform-paths section rather than by the usage skill                             |

Nothing the RED run produced is missing from the GREEN run: the baseline
produced two refusals and no findings to lose.

## Verification

Every command form the skill now states was RUN against the built program, and
the JSON field list was read off real output rather than off the source:

- [x] `--expand-virtual` lists the devices after `topology` and after `disks`,
      and appears in `tui --help`
- [x] `--set display.expand_virtual=true` does the same
- [x] `config-deploy --target user` writes `config.d/70-display.toml`, and that
      file carries `expand_virtual` under `[display]`
- [x] The thirteen disk fields and the six `bus` values are the ones a real
      `--format json` run emits
- [x] The Linux `bus == "virtual"` check: exit 0 on a clean machine, and it
      detects a planted virtual device, so it is not vacuous
- [x] The portable disjoint-lists check: both arms, same way
- [x] `snapshot -o` writes a document with no `disks` list and none of the four
      envelope keys, as claimed

## Checklist

- [x] Frontmatter unchanged; `description` measured at 536 characters
- [x] No workflow summary in the description
- [x] RED run dispatched in an isolated worktree, so the repo's gitignored
      `CLAUDE.md` - which documents this feature - was not in its cascade
- [x] Both runs asked for a `Skill gaps` section and both returned one
- [x] Each fix verified by quote-back: the GREEN run quoted the governing lines
- [x] No addresses, hostnames or machine paths added
- [x] Tests green: 989 passed, 6 skipped
