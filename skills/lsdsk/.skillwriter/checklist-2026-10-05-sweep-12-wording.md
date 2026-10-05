# Checklist - sweep 12: the macOS root store, the capped-card floor, USB 1 in MB/s, deep --set

## The changes this documents

1. A root run on macOS keeps the per-user store; only a root run on Linux defaults to
   `/var/lib/lsdsk/history.json`.
2. The capped-card action reads "a port with 16 lanes at PCIe Gen3 or faster" instead of "a port
   of at least PCIe Gen3x16", which read as met by a faster but narrower port.
3. A USB figure below a hundredth of a GB/s (USB 1) is written in MB/s: `USB12M (1.50 MB/s)`,
   `USB1.5M (0.19 MB/s)`.
4. A `--set` value nested deeper than 100 levels is refused at exit `2`.

## Change

- The store-location paragraph names Linux only for the root path and says root on macOS gets the
  per-user directory.
- The capped-hint paragraph quotes the new action and says both axes must hold, with the Gen5x8
  counter-example.
- The USB paragraph adds the MB/s form with both USB 1 figures.
- The exit-2 paragraph names the nesting limit beside "a malformed `--set`".
- No frontmatter changed. Every quoted figure and sentence was produced by the tool: the action
  text by `lsdsk findings` over the committed `linux-nvme-board` capture, the USB figures by
  `format_usb_sentence`.

## RED

- [x] Retrieval probe (text-only agent, weak tier, the four passages as they stood, the tool's new
      output pasted). Q1 root on macOS: answered `/var/lib/lsdsk/history.json`, wrong. Q3
      `USB12M (1.50 MB/s)`: "No, 1.50 MB/s is not the figure the documentation describes", wrong.
      Q4 deep `--set`: "does not specifically address nesting depth". Q2 answered correctly but
      quoted the old wording, which the tool no longer prints.

## GREEN

- [x] Same probe, new passages. Q1: the per-user directory, quoting "root on macOS included".
      Q2: Gen5x8 does not qualify, quoting the counter-example sentence. Q3: yes, quoting the MB/s
      rule. Q4: accounted for, quoting "nested deeper than 100 levels".
- [x] Diffed against RED both ways: Q2, the one RED got right, is still right and now quotes text
      that matches the tool.

## REFACTOR

- [x] GREEN gaps declined: `~` is the effective user's home by convention and needs no sentence;
      a general checklist for judging any port against the floor, whether a USB 1 figure varies,
      and the exact refusal boundary are the tool's output and `--help` to give, not the skill's.

## Deployment

- [x] Every test that reads SKILL.md passes (160, with the translation gate).
- [x] No address, host or path from a real machine added.
