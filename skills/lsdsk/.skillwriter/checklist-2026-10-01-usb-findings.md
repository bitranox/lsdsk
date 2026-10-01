# Checklist - the four USB link findings in "Reading a finding"

## The change this documents

lsdsk raises four findings about a disk's USB link: the USB 2 side of a USB 3 port,
a link below what both ends support, a drive capped by its port or a hub, and a
drive below its own maximum with the port not read. The findings table had no row
for any of them.

## Change

- Four rows in the findings table, beside the other link rows, each with what the
  finding means and what to do.
- One paragraph after the table: a USB disk is graded twice (its USB link, and the
  drive's own `link` inside the enclosure), how a USB figure is spelled (the whole
  link, lane rate times lanes, `USB10G` being either 10G x1 or 5G x2), how the
  title separates the two shortfall rows, and that `lsdsk slots` and
  `upstream_name` are PCIe only.
- No frontmatter, no field list, no exit code moved.

## RED

- [x] Retrieval probe (text-only agent, the findings section as it stood), four
      findings with the tool's real title wording. It answered NONE to none of them
      and mapped each onto a SATA or PCIe row by analogy: the USB 2 fallback onto
      "Link below what both ends support" (reseat, another port; no word of a USB 2
      hub or extension), the port cap onto "Held back by its controller" (a better
      HBA), and the unattributed shortfall onto the PCIe row, telling the user to
      read `upstream_name`, which no USB port has. Its gaps named the missing USB
      rows and the unexplained `USB480M`/`USB10G` spelling.

## GREEN

- [x] Same probe on the new section, with the capped finding in its hub wording.
      Each of the four was answered with a direct quote of its own row's Finding
      cell; "should I run `lsdsk slots`" and "where is its `upstream_name`" were each
      answered with a direct quote of the new sentence.
- [x] Diffed against RED: the one finding RED had right (both ends support -> reseat,
      another cable) is still right; nothing RED produced is missing.

## REFACTOR

- [x] Gap closed: GREEN told the both-ends row from the port-not-read row by
      inferring from the title wording. The paragraph now says the title decides it
      ("but both ends support" against "below its own"), which is the tool's own
      title text in `diagnose_usb_link`.
- [x] Quote-back on that question (F2 against F4, was the port read): the answer was
      a direct quote of the new title sentence, each finding then named its row, and
      the probe reported no gaps.
- [x] Gap declined: no per-row marker. No row of the table carries a severity, and
      the section already says to read the marker on the finding in front of you,
      which is what set the exit code.

## Deployment

- [x] No address, host or path from a real machine; the model names in the probes
      are generic product names, not the captured drives' serials.
- [x] Every speed spelling quoted was produced by `UsbSpeed.figure`, not written by
      hand.
