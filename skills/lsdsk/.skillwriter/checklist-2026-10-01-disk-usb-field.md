# Checklist - a disk carries `usb`, the USB link to its enclosure

## The change this documents

A disk in the JSON envelope gains `usb`: for a disk reached over USB, the link from
the machine to the bridge in its enclosure, while `link` stays the drive's own link
behind that bridge. Every other disk carries `usb: null`.

## Change

- The sentence enumerating a disk's fields names `usb` between `pcie` and `health`.
- One paragraph tail says what `link` and `usb` each describe on a USB disk, the seven
  fields of `usb`, the shape of a speed (`lane_rate` and `lanes`) and how to turn one
  into Gb/s.
- No frontmatter, no finding text, no exit code moved. The USB findings and the
  `USB10G` spelling are a separate change.

## RED

- [x] Text check of the artifact: `test_the_skill_enumerates_the_fields_a_disk_really_carries`
      failed before the edit with "only in the payload ['usb']", and passes after.
- [x] redcheck over this directory's cascade reported inherited coverage on four
      documents, matched on generic terms (bridge, link, speed, transport). Adjudicated:
      none of the three CLAUDE files contains the string `usb` at all, and no memory fact
      body contains `Disk.usb`. The text check stays the primary evidence.
- [x] Retrieval probe (text-only agent, current passage only), four questions: which
      field holds the USB link, what `link` describes on a USB disk, where UAS or BOT is
      read, and the field names of a USB speed. It answered NONE to the first three and
      took the SATA `link` object for the USB speed on the fourth; its gaps named the
      missing USB field and the unstated meaning of `link` behind a bridge.

## GREEN

- [x] Same probe on the new passage plus a fifth question (convert a `480M` and a `10G`
      speed to Gb/s). Questions 1 to 4 were each answered with a direct quote of the
      governing sentence.

## REFACTOR

- [x] Gap closed: GREEN could not quote a rule for question 5, because the passage gave
      the conversion only by one `10G` x2 example. The passage now says `M` is Mb/s and
      `G` is Gb/s per lane, that the speed is the lane rate times the lanes, and carries
      a `480M` example beside the `10G` one.
- [x] Quote-back on the contested question, `480M` x1 beside `5G` x2, the second a
      pair the passage does not spell out. It quoted the new rule sentence and
      answered 0.48 and 10 Gb/s.
- [x] Gap declined: what a `running` below `device_max` means is the findings section's
      to say, and the USB findings are documented with the change that adds them.

## Deployment

- [x] No address, host or path from a real machine; the passage names fields only.
- [x] The JSON shape quoted was taken from a real `UsbLink.model_dump_json()`, not
      written from the model source.
