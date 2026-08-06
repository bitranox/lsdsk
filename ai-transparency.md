# AI transparency

The author and owner of this project is the human, [@bitranox](https://github.com/bitranox).
Every design and engineering decision is theirs, and they answer for everything published
here. An AI assistant (Claude, run through the Claude Code CLI) was used as a tool along the
way, mostly for the typing and the legwork under that direction. This page says where, plainly,
so you can weigh the work on its merits. The reasoning behind working this way is in
[ai-stance.md](ai-stance.md).

## The human's work

The shape of this software is the human's, start to finish. They set the problem, made every
call, and own the result.

- The problem is theirs: a predecessor script that shelled out to `lspci`, `lsblk`, `smartctl`
  and `nvme`, regex-scraped their human-readable output, could not run on Windows, and reported
  nothing about wear.
- The central design rule is theirs, and it is what separates this tool from a facts dump:
  **severity is graded against what the machine could actually give.** A link below a device's
  own maximum is only a fault when something better is available, so every speed rule weighs
  what the device can do, what the other end can do, and what was negotiated.
- Theirs too, and each one changed the output: that a PCIe bridge is not a usable slot; that a
  proposal must consider which slots are genuinely free or swappable, because the graphics
  card's slot is not available; that width matters as much as speed; that the three speeds
  belong in three columns named port, disk and link rather than collapsed into one verdict;
  which colour each column carries and why; that the whole machine should be the default view
  because somebody who does not know what is wrong cannot know what to ask for; that the
  interactive pages must carry the same names as the commands; and that the SMART page should
  show every drive rather than demand a selection.
- The calls to keep the tool honest are theirs: report no value that was not measured, and say
  plainly which values could not be read.
- Shipping an AI-agent skill was the human's call, including that it should teach an agent to
  fetch a mainboard or controller manual when the tool itself stops, since lsdsk makes no
  network request by design.

## Where the AI was used

Under that direction, the assistant did the typing and the legwork: probing sysfs and the Win32
APIs on real machines to find out what is actually readable, decoding the ATA IDENTIFY, ATA
SMART and NVMe structures, writing the renderers and the test suite, and running the
verification described below.

It was also used to check its own work, which is where it earned its keep: measuring lsdsk's
output field by field against `smartctl`, `lspci` and `nvme` on live hardware, and finding
several places where the tool asserted something it had not measured.

## What's been checked, and what hasn't

Checked, on real hardware:

- Every reading compared field by field against `smartctl --json -x` across 19 disks on a
  storage server: 207 of 207 agreed, and the two apparent differences were proven to be a
  counter advancing between the two runs rather than a decoding fault.
- PCIe link state compared against `lspci -vv`, and physical slot numbers against its `SltCap`
  line, on 16 ports: identical.
- The AHCI capability decode compared against the kernel's own probe messages, which agree.
- Interface throughput measured directly to confirm an oversubscription finding: two SSDs read
  400 MB/s each alone and 206 MB/s each together, a shared ceiling exactly where PCIe 2.0 x1
  predicts.

Not checked:

- No physical Windows machine. The Windows path is exercised on a virtual machine and through a
  capture replayed on every CI runner, so the mapping is tested but the transport is not proven
  against real Windows hardware.
- No SAS expander, no hardware RAID controller, and no drive close to end of life.

## Checking it yourself

Nothing here asks for trust. `lsdsk snapshot` writes the raw reading, `--replay` renders any
capture through the identical decode and diagnosis path, and `--format json` emits it
structured, so any claim in the output can be checked against `smartctl`, `lspci` or `nvme` on
your own machine. The test suite replays captures taken from real machines, so a change that
breaks a reading breaks a test.

## What this isn't

This page is not a disclaimer, and it is not an apology. The work is the human's; the tool
helped them do it faster. If something here is wrong, it is theirs to answer for.

## License and attribution

MIT, as with the rest of the project. Attribution belongs to the human author.
