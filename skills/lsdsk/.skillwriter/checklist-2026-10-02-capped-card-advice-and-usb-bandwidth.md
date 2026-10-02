# Checklist - the capped-card advice, and a USB figure's bandwidth

## The changes this documents

1. The capped-card hint's action changed in two ways. "Not readable" is decided per port: it is
   said only where a free port that would carry more had its own connector bit unread, and that
   port is named. And where no free slot would help, it states a floor ("the card needs a port of
   at least PCIe Gen3x16") instead of naming one port.
2. Every USB finding writes its figure with its bandwidth, as the columns already did, so one 10G
   lane (`USB10G (1.21 GB/s)`) and two 5G lanes (`USB10G (1.00 GB/s)`) no longer read alike.

## Change

- The routing-table row for "is capped by its slot" adds the "was not readable" branch.
- The capped-hint paragraph says when "was not readable" appears and that the port is named, and
  that the no-slot action names a floor, not a particular slot.
- The USB paragraph says the bandwidth tells the two `USB10G` shapes apart in plain text, with
  both figures; the JSON `usb` object is kept as the outright answer.
- No frontmatter changed. Both quoted figures were produced by `format_usb_sentence` and the
  capped-card action text by `lsdsk findings` over the committed captures.

## RED

- [x] Retrieval probe (text-only agent, weak tier, the passages as they stood, the tool's new
      output pasted). Q1 "which port do I move it to" for the floor wording: answered from
      "the action names the port the card runs in full in" and had to reconcile that with output
      that names no port. Q3 one lane or two from text alone: NONE, "you need to run lsdsk with
      `--format json`". Q2 (not readable) answered correctly.
- [x] Its gaps named the mismatch between "names the port" and the floor wording, and that the
      text answer for USB required JSON the user did not have.

## GREEN

- [x] Same probe, new passages. Q1: no particular port, quoting "means any port at least that
      fast and that wide, not one particular slot". Q2: the named port, re-run as root, quoting the
      new sentence. Q3: one 10G lane, quoting the two figures.
- [x] Diffed against RED both ways: Q2, the one RED got right, is still right.

## REFACTOR

- [x] GREEN gaps declined: "why no free slot exists" and "what the root re-run will show" are the
      tool's output to give, not the skill's; Q3 reported none.

## Deployment

- [x] Every test that reads SKILL.md passes.
- [x] No address, host or path from a real machine added; `0000:00:1c.4` appears only in the
      probe's pasted input, not in the skill.
