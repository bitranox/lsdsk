# Command reference

Every command, every global option, the JSON envelope and the exit codes.
Back to the [README](README.md).

## Commands

| Command                          | Shows                                                                                              |
|----------------------------------|----------------------------------------------------------------------------------------------------|
| `lsdsk`                          | At a terminal, the interactive view. Anywhere else, the page below                                 |
| `lsdsk report`                   | Everything on one page. Each command below is one section of it                                    |
| `lsdsk topology`                 | The problem summary, then the whole PCI fabric root-down with each controller's disks nested on it |
| `lsdsk controllers`              | Controllers, PCIe placement, free ports, load                                                      |
| `lsdsk disks`                    | One row per disk                                                                                   |
| `lsdsk health`                   | Wear, temperature, hours and error counters                                                        |
| `lsdsk smart`                    | Every disk's SMART attributes against its thresholds                                               |
| `lsdsk findings`                 | Every finding with its reasoning and its remedy                                                    |
| `lsdsk slots`                    | Every PCIe port: capability, occupant, what is free                                                |
| `lsdsk trend`                    | What each error counter is doing over time, not just its total                                     |
| `lsdsk record`                   | Store one reading and print nothing, for a timer                                                   |
| `lsdsk tui`                      | An interactive page per question, `*top` style                                                     |
| `lsdsk snapshot -o f.json`       | Capture the raw reading                                                                            |
| `lsdsk --replay f.json`          | Render a capture from any machine                                                                  |
| `lsdsk config`                   | The merged configuration, and which layer each value came from                                     |
| `lsdsk config-deploy`            | Write the shipped defaults where you can edit them                                                 |
| `lsdsk config-generate-examples` | Write commented example files without touching live config                                         |
| `lsdsk info`                     | Version, homepage and the metadata a bug report needs                                              |

Every option below is global: it goes before the command, and it applies to
whichever command follows. `--expand-virtual` is also accepted after `topology`,
`disks` and `tui`, because that is what the line tallying those devices tells
you to type. `--tree-density` is also accepted after `topology`, and the
interactive view cycles the same setting with `d` on its topology page. Every
view opens on `storage-only` and says in a line above the tree what it draws and
how to ask for the rest.

| Option                                                    | Does                                                                     |
|-----------------------------------------------------------|--------------------------------------------------------------------------|
| `--replay FILE`                                           | Render a capture instead of reading this machine                         |
| `--history-file F`                                        | Read and write counter history there instead of the per-user state file  |
| `--no-record`                                             | Judge counters against history without adding this reading to it         |
| `--expand-virtual`                                        | List every kernel-virtual device instead of tallying them in one line    |
| `--tree-density storage-only\|storage-and-siblings\|full` | How much of the PCI fabric every view draws (default `storage-only`)     |
| `--profile NAME`                                          | Load a named configuration profile                                       |
| `--set S.K=V`                                             | Override one configuration value, repeatable                             |
| `--env-file FILE`                                         | Read that `.env` rather than searching upward from the working directory |
| `--traceback` / `--no-traceback`                          | Print the Python traceback on an error instead of one line               |
| `--version`                                               | Print the version and exit                                               |

`lsdsk disks` takes one option of its own. The `wwn` column is held to
`display.wwn_width` characters, because an NVMe WWN is five times the length of
the SATA ones beside it and would otherwise set the column's width for every
row; a cut value is marked rather than shortened in silence, and on the
interactive page it stays readable in full in a strip under the table, which
`,` and `.` scroll; neither key is offered on any other page.
`--full-wwn` prints the whole identifier instead, and lays the table out wider
than the terminal rather than buying the width from the columns beside it, so
the row runs off the side and a pager scrolls it (`lsdsk disks --full-wwn |
less -S`). The JSON envelope always carries every WWN in full, whatever the
human view was asked for.

`--format json` gives a machine-readable envelope naming the command that
produced it, on every command that produces data except `report`, whose
machine-readable form is `lsdsk snapshot`. Exit codes are `0` for nothing
actionable and `1` when a warning or critical was found, so it drops straight
into a monitoring check. Errors use sysexits conventions rather than a single
code: `13` when something needs privilege this run lacks, `22` for a
configuration section or a `--profile` name the configuration library
rejects, `78` for a file that is not a
snapshot this version reads or a platform with no hardware reader. Treat
anything above `1` as "did not run".

`2` is Click's usage error and means the command line was wrong, not that a file
was missing: an unknown option, an unknown command, a missing argument and a bad
`--format` choice all produce it, alongside a `--replay` path that is not there.
