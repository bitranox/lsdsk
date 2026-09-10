# skill-writer review: eight false claims a documentation audit found

A documentation audit checked ~861 claims across 11 documents against the running
tool. Eight of its findings land in this skill. Each was re-verified here before
the wording changed, and two of the audit's own claims did not survive that
re-verification.

## RED

The three monitoring snippets are executable, so the failing test is the snippet
itself rather than a judgement call. Each was run exactly as shipped, against a
machine whose findings are hint-only:

    $ lsdsk findings --format json | python3 -c 'import json,sys; d=json.load(sys.stdin);
      sys.exit(any(f["severity"] == "critical" for f in d["data"]["findings"]))'
      File "<string>", line 2
        sys.exit(any(f["severity"] == "critical" for f in d["data"]["findings"]))
    IndentationError: unexpected indent
    rc=1

The continuation line is indented, which inside `python3 -c` is an
`IndentationError`. It exits `1` - the value these checks use for "found" - so a
monitor built from the shipped text reports a critical on a machine that has
none. The control matters here: that machine's only severity is `hint`, so the
correct answer was `0`. The second snippet failed identically; the third has the
same shape.

## GREEN

The corrected snippets were extracted back out of the file and executed, so the
shipped text is what ran rather than a retyped copy. All three exit `0` on the
hint-only machine.

The critical branch cannot be exercised from a fixture - none of the five carries
a critical finding - so it was proved against a crafted envelope through the same
program: `1` with a critical present, `0` with only hint and warning. Without
that arm the check would have passed by always answering `0`.

## Two audit findings rejected

| Audit finding                                                   | What re-testing showed                                                                                                                                                                                                        |
|-----------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `config-generate-examples` rejects `--profile`, exit 2          | It is accepted before the command and rejected after it, like every global option. The real asymmetry is that `config` and `config-deploy` accept it AFTER the command and this one does not, which is what the text now says |
| The env switch that skips the venv sync is `BMK_NO_VENV_SYNC=1` | No such name exists in the installed bmk, so nothing was substituted (this one belongs to CONTRIBUTING.md, not here)                                                                                                          |

## Verification

Every changed claim was re-measured against the built program:

- [x] `report --help` lists only `--replay` and `--help`; `report --format json` exits 2
- [x] `report` sets 0/1 from findings: exit 1 on the SAS fixture, 0 on the minimal one, so it is a ninth command that does
- [x] A SATA row carries the `link` gbps triple with `pcie` null; an NVMe row leaves that triple null and carries `pcie` with `current_speed_gtps`
- [x] The sub-one trend branch prints "this drive's rate would not have produced even one in <span>h" and never a rounded "only <n> were due" (`trend.py`)
- [x] A CRC count below `crc_errors_significant` starts as a hint, and history raises severity by one step, so the climbing case reads as a warning rather than a critical
- [x] The `nvme.<vendor>-<serial>-<model>-<nsid>` wwn form ships in a committed capture, so it is a third form a reader will meet
- [x] `--profile` after `config` and `config-deploy`: accepted. After `config-generate-examples`: exit 2
- [x] The three snippets run from the file, both arms where an arm exists

## Gaps

| Gap                                                                    | Outcome                                                                                                                   |
|------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| The history-file example used a bare filename, writing into the cwd    | CLOSED. The example now gives the path in full and says why                                                               |
| No fixture carries a critical finding, so that arm is unexercised here | DECLINED. Proved against a crafted envelope instead; adding such a fixture is a change to the captures, not to this skill |

## Checklist

- [x] Frontmatter untouched; no description change, so no routing keyword moved
- [x] RED run is executable and was recorded verbatim, with a control that makes its verdict meaningful
- [x] GREEN tested the shipped file, extracted from it rather than retyped
- [x] Both directions checked where a check has two branches
- [x] Every corrected claim re-measured against the running tool, not taken from the audit
- [x] Two audit findings rejected on evidence rather than applied
- [x] No addresses, hostnames or machine paths added
