# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.

## [Unreleased]

## [1.2.12] 2026-09-12 02:09:28

### Fixed

- **A port that can do more than the PCIe floor holds a part, not a function of
  the switch.** A function built into switch silicon sits behind an internal port
  that publishes the floor as its own capability, while a real downstream port
  passing a link to a separate card publishes the switch's. So a port capability
  that was read and reads above the floor now refutes the register-default hint,
  on the controller's own port and on the twin's, which the vendor check cannot
  do when a card and the switch share a maker: a card that genuinely is Gen1 x1,
  plugged into a switch of its own maker beside another floor-reading device,
  completed every other leg of the pattern and was excused as a register default.
  A port whose capability was not read refutes nothing, which is every Windows
  bridge, so judging there is unchanged.

## [1.2.11] 2026-09-11 16:22:19

### Fixed

- **The capped-by-the-mainboard hint judges a port by what it would give the
  card.** A port was ranked by its own capability, so a PCIe 3.0 x16 port counted
  as faster for a PCIe 4.0 x4 drive it can only give PCIe 3.0 x4, the speed of one
  port could pair with the width of another, and freeing it was promised to take
  the drive to its own maximum. Now freeing a port quotes what that port would
  deliver, and where no port gives more the hint names a board of the card's own
  generation, not the next one up, or the port the card needs where the board
  already has that generation.
- **An NVMe drive with several namespaces is counted as one drive.** Each
  namespace is its own block device carrying the drive's PCIe link, so the
  oversubscription check added a drive's link up once per namespace and could
  warn that a drive with two namespaces was oversubscribed by itself, and the
  capped-by-the-mainboard hint said such a drive wanted twice what it can pull.
  The load column of the controllers table summed the same way. Namespaces now
  fold into their drive where they share a controller and a serial number;
  drives whose serial was not read, or that only share a tri-mode HBA, still
  count separately.

## [1.2.10] 2026-09-11 14:54:30

### Changed

- **Moving or swapping a card is a warning only when the drives on it would
  notice.** A controller in a slower slot than it supports was told to move
  whatever it carried, two hard disks that could never use the difference
  included. Where the attached drives fit the slot the controller already has,
  the finding is now a hint that still names the faster slot for when more
  drives are added. A PCIe drive is weighed by what its link can carry rather
  than the speed it rests at while idle, which also corrects the
  capped-by-the-mainboard hint's sentence about whether the drives feel the cap.
- **The skill covers the graded move and swap advice and the vendor check behind
  the floor hint.** Its finding table gains rows for a faster slot or a swap the
  drives on a controller would not notice, the move row now means the drives
  would use the faster slot, and the floor section says both devices at the
  floor must carry the vendor of the switch ports in front of them, naming the
  JSON fields that show it.

### Fixed

- **A replay whose sections have the wrong shape is refused as a bad file.**
  Only a snapshot's outer keys were checked, so a wrong-shaped section - text
  where the block devices, the PCI devices, the sysfs classes or the Windows
  disks belong - reached a builder and failed there with a traceback under the
  wrong exit code, and a value of the wrong type, such as a numeric board name,
  was dropped without a word. Every section a builder reads is now typed where
  the reading enters, for a live run and a replay alike, and a file that does
  not fit is refused with the configuration exit code and the place it went
  wrong.
- **A board vendor that begins with a comma no longer crashes a scan.** Joining
  the vendor to the board name took the vendor's first word, and a vendor such
  as ", Inc." has none.
- **A hypervisor flag held as the text "false" no longer marks a machine as a
  virtual machine.** The flag was tested for truthiness, and any non-empty text
  is true.
- **A single drive is counted as one.** A controller with one drive on it read
  "1 drives can pull about ... together" in the oversubscription warning and
  "The 1 attached drives need about ..." in the capped-by-the-mainboard hint,
  and the trend view said "1 drives are on record" for a single recorded drive.
  Each now speaks of the drive.
- **A Windows disk behind a USB bridge is attributed to the controller above
  it.** The reader recorded only the device a disk hangs off, which for a USB
  disk is its mass-storage device rather than the host controller, so such a
  disk had no controller where the same disk on Linux names one. The reader now
  records every device above a disk and the builder takes the nearest PCI one;
  a snapshot taken before still reads its parent as it did.
- **The PCIe-floor hint no longer hides a real bottleneck because a separate
  device reads the same floor.** It stood in for the oversubscription warning
  when a second device on the same switch published 2.5 GT/s x1 beside a real
  link, and a separate part genuinely linked at that speed, such as a network
  or FireWire controller, satisfied that as well as a function built into the
  switch. The controller and that second device must now both carry the vendor
  of the switch ports in front of them, and `lsdsk slots --format json` carries
  both identifiers for every port so the reading can be checked.
- **The PCIe-floor hint needs a device on the switch actually running a real
  link.** A drive capable of PCIe 4.0 x4 that trained at 2.5 GT/s x1 counted as
  one, although with it every device on the switch runs at the floor, which is
  what a switch narrow everywhere produces.
- **The capped-by-the-mainboard hint no longer calls a controller empty when a
  drive's link was not read.** A drive whose rate is not read, such as a RAID
  logical drive, was left out of the demand, so the hint said nothing was
  attached or let the drives that were read stand for all of them. It now says
  what was not read.
- **The test suite no longer deletes other runs' coverage data.** Its setup
  removed the coverage database in the system temp directory at the start of
  every pytest session run without `COVERAGE_FILE`, so a plain pytest run left
  an earlier coverage run with no data to report. The redirect the same setup
  claimed to apply never reached pytest-cov, which creates its database before
  any conftest is imported, so both are gone.

## [1.2.9] 2026-09-11 03:00:00

### Changed

- **The skill's finding table no longer offers an HBA as an equal remedy for an
  oversubscribed controller.** The row now says the uplink figure can be a
  register default, points the reader to the section that says to check that
  figure first, and puts moving a drive onto a controller already fitted ahead of
  buying an HBA, as that section does.
- **The skill knows the floor hint and what proves a switch.** Its finding table
  gains a row for a controller publishing the PCIe floor, the oversubscription
  section says when lsdsk recognises that pattern itself and when the check stays
  the reader's, and reading ports that share a bus as one switch now requires a
  bridge recorded above that bus, since ports directly on a root bus are
  independent slots.

### Fixed

- **lsdsk no longer tells the owner of a PCIe expansion card to replace working
  hardware.** A SATA controller integrated into a desktop chipset used as a PCIe
  switch publishes the PCIe floor, 2.5 GT/s x1, as both its running and capable
  link, and lsdsk read that register as a 0.25 GB/s ceiling and called the
  controller oversubscribed. When the controller's link reads the floor in both,
  another function on the same switch reads the identical floor, and a third
  device there has a real link, it now reports a hint that the figure is a
  register default rather than a ceiling. A switch is proven by a bridge recorded
  above its bus, so ports directly on a root complex, which are independent
  slots, are judged as before.
- **Moving the cursor off a row whose WWN was scrolled no longer paints one frame
  of the next identifier still scrolled.** The strip's rewind was queued until
  after the next refresh, which laid out and painted the new identifier at the
  old offset first; it now applies at once. The same window is what made one TUI
  test fail at random on a slow CI runner.

## [1.2.8] 2026-09-11 00:50:18

### Fixed

- **`lsdsk config` names the command line as the source of a `--set` value.**
  An override replaced the value but kept the provenance of the layer it
  replaced, so `lsdsk --set display.wwn_width=8 config` printed 8 under the
  shipped default file's path, and following that answer led to a file holding
  24. The library's merge shares the original provenance map by design; the
  override path now rebuilds it, recording `cli` with no path for exactly the
  keys `--set` supplied.

### Changed

- **`snapshot` says what a capture carries.** Its help, and a line on stderr
  after the file is written, now name the drive serial numbers and the hostname
  a capture holds. The command describes a capture as a bug report and nothing
  said the file identifies the machine. The line goes to stderr in both output
  modes, so the `Wrote <path>` line and the JSON envelope a script parses are
  unchanged.
- **The shipped skill says a snapshot and the history store carry the
  hostname.** It named drive serial numbers alone, and the passage that tells
  a reader to attach a snapshot to a ticket said nothing about what the file
  identifies. Both now do, matching what `snapshot --help` and its write-time
  notice say.

## [1.2.7] 2026-09-10 20:08:28

### Changed

- **The shipped skill can now overturn a finding, not only fill in what lsdsk
  cannot measure.** It offered published documentation as a supplement only, so
  a reader had no way to disbelieve a figure the tool did report. The
  arbitration now has a direction: a measured throughput above a reported
  ceiling refutes it, because a ceiling cannot be exceeded, while a published
  figure below the reading settles nothing, since that is what a downtrained or
  shared link looks like.
- **The oversubscription finding has a section of its own.** Its two remedies
  are put in order, moving a drive onto a controller the machine already has
  before fitting an HBA, and three readings mark an uplink figure as a register
  default rather than a data path. The discriminator is a second integrated
  function on the same silicon publishing the identical floor, not the floor
  value alone, because a dead link reads the same.
- **A controller row does not say whether the silicon is a card or soldered to
  the board**, so the shipped remedy naming a card is a template rather than an
  observation, with the two readings that usually settle it.
- **The controllers table's columns are defined**: `running` and `capable` are
  the controller's own PCIe link, and `load` is a capability sum of the attached
  drives' negotiated links rather than a measurement of traffic.

## [1.2.6] 2026-09-10 18:22:35

### Fixed

- **A controller's ceiling is what its link can carry, not what it happens to
  be carrying.** A PCIe link drops to 2.5 GT/s while the device behind it is
  idle and retrains when work arrives, so reading the resting figure reported a
  controller as oversubscribed on hardware that has no bottleneck at all.
  Measured on one machine minutes apart: the same graphics card read x4 at
  8.0 GT/s with its core at 1265 MHz, and x4 at 2.5 GT/s with it at 151 MHz. A
  link that is genuinely stuck below what both ends support is a different
  fault, and the controller-link rule already names it with the remedy that
  fits.

## [1.2.5] 2026-09-10 17:20:36

### Fixed

- **The skill's three monitoring snippets always reported a fault.** Each ran a
  `python3 -c` program whose continuation line was indented, which is an
  `IndentationError` and exits `1` - the same code the checks use for "a
  critical was found". A monitor built from them alerted on every machine,
  including one whose findings were hints only.
- **`report` was documented as taking `--format json`** in four places. It does
  not, deliberately: the machine-readable form of the whole page is
  `lsdsk snapshot`.
- Corrected the documented severity of a climbing CRC count (a count below the
  significance floor starts as a hint and history raises it one step, so it
  reads as a warning, not a critical), the structured speed fields for SATA and
  SAS drives (Gbit/s under `link`, with `pcie` null, rather than PCIe GT/s), the
  wear thresholds the sector and media counters are judged against, and the
  third `wwn` form an NVMe drive can publish.

### Changed

- Documentation corrections across README, CONFIG, INSTALL, DEVELOPMENT,
  CONTRIBUTING, SECURITY, the module reference, ADR 0001 and the AI stance,
  each re-verified against the running tool. Notably: an override file must
  live in a layer's `config.d/`, since a file beside `config.toml` is read by
  nothing; `uv sync` alone installs no development tools, because `dev` is an
  extra; and `make release` tags the committed version rather than bumping it.
- Two claims are now held by tests rather than only stated. The marketplace
  manifest's version is pinned to `pyproject.toml`, so a release cannot ship a
  stale one that no install re-fetches, and the default page's completeness
  guard now covers its eighth section.


## [1.2.4] 2026-08-29

### Fixed

- **`lsdsk config` printed a list of secrets in full.** The second redaction
  pass hid a scalar under a sensitive-looking key and dropped that key on its
  way into a list or tuple, so nothing inside one was ever judged: `password`
  holding one secret was replaced and the same name holding several was
  printed. The key now governs every item beneath it, however deeply the lists
  nest, while a table under such a name still keeps its readable half, because
  a section named `auth` legitimately holds a username and a host beside its
  token.

### Changed

- The redaction walk is typed by a recursive `ConfigValue` rather than `Any`,
  so the shapes it returns are checked rather than assumed, and the two casts
  it needed are gone. The alias uses `Mapping` and `Sequence` rather than
  `dict` and `list` because those are invariant in their contents: an ordinary
  nested literal is not assignable to a `dict[str, ConfigValue]` parameter
  however plainly it is one.
- **A plural is now hidden like its singular.** `token` was recognised and
  `tokens` was not, which is the spelling a configuration file uses precisely
  when it holds several. A word is tried without its trailing `s` in addition
  to the word itself, never instead of it, because folding first turns `pass`
  into `pas` and loses a match it already had. What only means a secret in
  company still does: `api_keys` is hidden and a bare `keys` is not.
- One limit remains, and is now pinned by a test rather than left to be
  discovered: a name written as one squashed word (`privatekey`, `apikey`) is
  not recognised, because matching is against whole words. Widening that to a
  substring test would also blank any ordinary name containing a sensitive
  word, which hides the configuration a reader came to see.

## [1.2.3] 2026-08-28

### Fixed

- The shipped Claude Code skill still printed the trend table's old `over`
  header in its rendered example, so a reader who parses that table by column
  was told to key on a header 1.2.2 no longer emits. Both documents that print
  the table now derive their header from `TREND_COLUMNS` in a test, the README
  included, so an example cannot name a column the renderer does not.
- That skill also described what an elevated run records without saying what
  1.2.1 changed: a run that records covers every drive it read, and a drive
  whose own power-on hour has not advanced has its newest row replaced rather
  than gaining one. It read as though a run stores one reading in total rather
  than one per drive, and it named a per-drive cap without naming the key that
  sets it.

## [1.2.2] 2026-08-28

### Changed

- The trend view's `over` column is now called `span`. It carries the window the
  `change` figure covers, measured per counter, so one drive legitimately shows
  a different figure on every row; headed `over` it read as a property of the
  drive, and three rows for one NVMe drive at 531h, 78h and 6h looked like three
  contradictory answers to what its power-on hours were rather than one answer
  each for media errors, the error log and wear. The new name is the same four
  characters, so the column keeps its width on a table that gives up columns as
  the terminal narrows. The interactive Trend page renders through the same
  function and follows automatically.

### Fixed

- The trend table had no test of any kind: nothing referenced `render_trend`,
  `TREND_COLUMNS` or `trend_row`, so its headers and rows could drift unnoticed.
  It is now rendered in a test that reads the header off the real output.
- The README's trend example showed four different spans without saying why they
  differ, which is the misreading the rename exists to prevent. It now says.

## [1.2.1] 2026-08-28

### Fixed

- **A finding's severity can now RISE on an existing store, so a job gating on
  the exit code may newly fail on hardware that has not changed.** That is this
  release working: a fault that is actively happening is graded as one. On the
  machine this was found on, `/dev/sdd` moves from warning to critical.
- The trend view called a counter unmeasurable on exactly the drives whose
  counters were moving. A reading is recorded when SOME drive's clock has
  advanced, and a run that records at all stores a row for EVERY drive, so a
  drive whose own clock stood still collected several rows inside one power-on
  hour; judging compared only the newest two, which on such a drive is a reading
  against itself. Measured: 43 rows per drive covering 15 power-on hours, 14 of
  19 drives holding a duplicated newest pair, and the two counters climbing
  fastest in the machine, one of them at 5776 CRC errors an hour, both rendered
  `+0, too soon to say`. Both ends now hold the rule that two rows in one hour
  carry one hour of information: recording folds a repeated hour into that
  drive's newest row, keeping the later reading because these counters only
  climb, and judging compares against the newest reading from a different hour.
  Where every reading sits in one hour the row behind is still used, so a rise
  inside a single hour is seen and still refused a rate.
- Those drives also regain their rising marker on the health page and the
  sentence naming what a count gained over what span, which had been reaching
  every drive except the ones that were failing.
- A refusal measuring a zero-hour span said "no power-on hours have passed since
  the first reading", which named the wrong thing. The span runs from where the
  counter last moved, not from when recording began, and a store holding 531
  power-on hours of readings was being described as its first one.

## [1.2.0] 2026-08-28

### Added

- The `wwn` column is held to `display.wwn_width` characters, 24 by default, in
  the printed table and on the interactive disk page alike. An NVMe WWN runs to
  a hundred characters where the SATA ones beside it run to twenty, so on a
  machine with one NVMe drive that single identifier set the width of the column
  for every row: the page pushed nine columns off the screen, and the printed
  table spent a hundred columns on one row and left the other ten as gutter.
- A cut value stays readable in full on the disk page, in a strip under the
  table carrying the whole identifier of the row the cursor is on. The strip is
  exactly as wide as the column, which is what makes its scroll control appear
  on the values the column had to cut and on no others. `,` and `.` move it.
- `lsdsk disks --full-wwn` prints the whole identifier instead, laying the table
  out wider than the terminal rather than buying the width from the columns
  beside it, so the row runs off the side and a pager scrolls it. Raising the
  ceiling alone would not do this: the fitter shrinks a flexible column to its
  minimum long before it drops anything, and the renderer then compresses every
  column again to reach the terminal width.

### Changed

- Every printed table now marks a cut cell the same way the topology tree
  always has, with an ASCII `>` rather than an ellipsis character, so one drive
  reads the same in every view and the mark survives a cp1252 console.
- `lsdsk disks` caps the wwn column by default, where a wide terminal
  previously printed the whole identifier. `--full-wwn` restores it and
  `display.wwn_width` raises the ceiling.
- `LsdskApp.__init__` takes `display=`, a `DisplaySettings`, where it took
  `expand_virtual=`. One object rather than a keyword per value, so a page
  reads the same settings the printed command of its name reads and a second
  delivery path cannot leave one of them deaf. A caller constructing the app
  directly has to change.

The JSON envelope is unchanged and carries every WWN in full, whatever the
human view was asked for.

## [1.1.1] 2026-08-28

### Fixed

- The interactive disk page now carries the drive's serial and firmware. A
  drive is identified by model, serial and firmware together - two disks of one
  model differ by serial, and a firmware revision is what a mixed-firmware
  finding sends the reader to check - and the page named only the model, so it
  could not answer the question its own finding raises. The page was built from
  its own column tuple rather than from the printed table's, so it never had
  either: the tuple was written that way at 1.0.0 and nothing compared the two.
  A test compares them now.

## [1.1.0] 2026-08-27

### Changed

- Kernel-virtual block devices are shown rather than dropped. 1.0.8 stopped
  them at read time, which fixed the RAM disk that was failing every check but
  made hardware the machine really has vanish from its own inventory. They are
  kept and labelled now: `Inventory.disks` stays the physical drives every rule
  and every reading is about, and the new `Inventory.virtual_disks` carries
  zram, loop, zvol and device-mapper nodes on `BusType.VIRTUAL`.

- The topology tree and the disk table end with a tally of them - "12 not
  listed: 8 loop, 3 zd, 1 zram" - and `--expand-virtual` gives each one a row.
  Folded away by default because a Proxmox host has more of these than drives,
  and forty zvols would push the real drives off the view that exists to show
  them. Both views take that sentence from one function, so they cannot
  describe one machine differently, and the header counts the devices apart
  from the drives rather than adding them in.

- `--expand-virtual` is accepted before the command like every global option,
  and also after `topology`, `disks` and `tui`, because the tally names it. It
  lands on the new `display.expand_virtual` key, so a file, the global flag and
  a subcommand flag are one setting.

- The JSON envelope gained `data.virtual_disks`, always populated whatever the
  display setting says. The tally is a screen-space decision; a program parsing
  the output needs the whole machine.

### Fixed

- An optical drive is no longer hidden. The retired name-prefix list excluded
  `sr`, which is real hardware occupying a real AHCI port, so the port count
  was short by one on any machine that has one.

- A virtual device no longer reports a media kind it was never told. The
  builder mapped `queue/rotational`, which reads 0 for loop, zd and zram alike
  because it is a default the kernel fills in for a device with no media, so a
  zvol on a pool of spinning disks reported solid state.


## [1.0.8] 2026-08-27 19:53:27

### Fixed

- The console adapter no longer asks click for the stream it is about to write
  to. click 8.5.0 deprecated `get_text_stream` (removal in 9.0) and routed it
  through a module `__getattr__`, so its type reads as unknown and the strict
  type gate failed at every call site. `echo` hands `file=` and `err=` to click
  unchanged now, and reads the encoding it judges the ASCII fallback against
  from `sys.stdout` or `sys.stderr`, which is where click resolves its own
  default target from. Those are the same stream wherever it matters: click
  returns `sys.stdout` untouched unless it is misconfigured to ascii, and on a
  Windows console it returns a `utf-16-le` writer while `sys.stdout` is already
  utf-8 under PEP 528, so both accept the glyph.

- `load_history`'s doctest no longer asks whether `/nonexistent/history.json`
  exists. On a Debian or Ubuntu box `/nonexistent` is the `nobody` account's
  home at mode 0700, so the stat raises `PermissionError` instead of answering
  no, and `Path.exists()` only stopped propagating that in Python 3.14. The
  example uses a directory it creates, so the missing file is missing because
  the example says so. CI never saw it: its runners have no such directory, and
  the doctest is only reached on the Python versions `make test-all` covers.

- A RAM-backed block device is no longer read as a disk. `zram0` reached the
  inventory on every Proxmox host here as a disk on a bus called `unknown` that
  reports no counters and never can, because the reader decided what was
  physical from a list of NAME PREFIXES and `zram` matches none of them (`ram`
  does not). It asks the kernel now: a device with no physical parent resolves
  under `/sys/devices/virtual`, which is a fact read from the system rather than
  an inference from something being absent, and it covers the next such device
  without anyone adding its name. Verified against real sysfs on a host: every
  real drive answers no, every `loop`, `zd` and `zram` answers yes.
- The real-hardware test suite ships a build the host cannot serve from cache.
  `uv run --reinstall --with <wheel>` keys uv's unpacked archive on the wheel's
  name and version, neither of which changes between builds of one version, so a
  re-run silently exercised the PREVIOUS build. Measured: a byte-identical wheel
  containing this fix loaded a module without it, and the suite reported the bug
  still present after it had been fixed. `--no-cache` is the only thing that
  reaches it; `--reinstall` and `uv cache clean lsdsk` do not.

### Added

- Tests for `echo`'s default target. Every existing case passed an explicit
  `file`, so the branch this had to rewrite had no coverage at all.

## [1.0.7] - 2026-08-07

Version 1.0.6 reached the plugin marketplace from the default branch and was
never published; the bundled skill changed after that, and a marketplace install
only re-fetches on a version change.

### Changed

- The whole-machine page is a command, `lsdsk report`, and the `--report` flag
  that 1.0.4 introduced is gone. Every section of that page was already a
  subcommand, so the page being a flag was inconsistent on its face, and it
  needed a rule nothing else here does: a flag that picks a view in one position
  and is refused in another. The reason recorded for keeping it out of the
  command list was that a command would duplicate what a bare `lsdsk` already
  printed - which stopped being true when the interactive view took that
  position, leaving the page with no name at a terminal at all. Removed rather
  than kept as an alias because the flag was hours old and published the same
  day.
- The README documents every command and every global option, in a reference
  table rather than only in prose. `--report` had a paragraph and no entry;
  `config`, `config-deploy`, `config-generate-examples` and `info` had never
  been listed at all. The commands row for a bare `lsdsk` still described it as
  the page, which the interactive default had made wrong.

### Added

- The bundled skill states the machine-readable contract instead of implying it.
  A finding's five fields are named, `severity` is pinned to exactly `critical`,
  `warning` or `hint`, and the four envelope keys get their meanings - `ok` says
  the command finished what it was asked, never that the hardware is healthy. It
  also says that a monitor has to read `data.privileged`, because an
  unprivileged run raises no SMART finding at all and a severity check alone
  reports clean on a machine nobody looked inside. And it documents the
  in-process path, `snapshot.load` plus `diagnose`, for a caller who does not
  want a subprocess.
- Three tests hold the README against the CLI: every registered command is
  named, every global option is named, and no option is documented that the
  program refuses. Documentation drift is silent, and nothing was asking.

## [1.0.5] - 2026-08-07

### Fixed

- A controller is named the same whatever the machine reports it. Windows
  localises its own device descriptions, so a German install showed
  "Standardmaessiger NVM Express-Controller" beside English column headings, and
  two machines with identical hardware disagreed about what was in them. The
  numeric vendor and device identifiers say it in one language, and resolving
  them is what Linux already did.
- Our own source carries no typographic characters. Four files had an em-dash in
  a docstring or a comment, against the project's own ASCII rule.

### Added

- The PCI name database ships with the package, because resolving those
  identifiers needs one and no Windows machine has a `pci.ids`. It is trimmed to
  the vendor and device lines this tool reads, which is 210 KB compressed, and
  where the system has its own copy that one is preferred, since a distribution
  refreshes it more often than this tool is released. See NOTICE: it is
  redistributed under the 3-clause BSD License.

The name Windows gives a PCIe PORT is untouched. It is quoted, in quotation
marks, because Windows publishes no link registers for a bridge and a name like
"Intel(R) PCIe RC 060 (x4) G4" is the only statement about that port's width and
generation. Substituting a quotation would discard the one thing it carries.

## [1.0.4] - 2026-08-07

Mostly about what the output looks like and who it is for.

Version 1.0.3 reached the plugin marketplace from the default branch but was
never published to PyPI, and its bundled skill still described a bare `lsdsk`
as always printing to a subprocess. This release supersedes it.

### Changed

- A bare `lsdsk` opens the interactive view at a terminal and prints the page
  everywhere else. The whole machine on one page is more than a reader takes in
  at once, but a full-screen application cannot run into a pipe, so the switch is
  what the output IS rather than a flag. Every existing pipe, redirect, CI run
  and agent keeps the printed page, and the exit code is the findings' in both,
  so `lsdsk; echo $?` means one thing regardless.
- The palette is readable on a light background as well as a dark one. Measured
  as WCAG contrast against four real terminal backgrounds, the previous hint and
  ceiling colour scored 1.7:1 and the warning colour 2.4:1 on a light background,
  where the floor for large text is 3.0. Two causes compounded: the faint
  attribute, which terminals implement by blending toward the background and
  which therefore removes the contrast it is asked to provide, and naming a
  colour the terminal resolves through its own palette. Nothing is faint any
  more, including column headers and whole columns of values that were dimmed
  wholesale, and every colour is now the most legible member of its hue at
  4.2:1 worst case.
- A trend refusal no longer reports an expectation that rounded away. "only 0.0
  were due" stated that none were due and left "only" contradicting its own
  number; a drive with one lifetime CRC error in 42278 hours predicts 0.000024 of
  one across an hour. Zero elapsed time and a real but sub-unit expectation now
  say what they each mean.

### Added

- `lsdsk --report` prints the whole-machine page whatever the terminal looks
  like. The switch above reads the terminal, and there is one thing it cannot
  see: a caller that allocates a pseudo-terminal for a program. IPython runs
  `!cmd` under pexpect, so a notebook cell presents a terminal on both ends and
  is indistinguishable from somebody sitting at a shell; `script`, expect and
  pty-allocating job runners do the same. The interactive view then waits for a
  keypress that has no keyboard behind it. Detection cannot be made to cover
  this, so the flag is the way out, and it is what anything unattended should
  say. Given before a subcommand, where it has no view to choose, it is refused
  rather than silently ignored.
- The version rides in the report banner and the interactive view's title. This
  output gets pasted into tickets, where the first question asked of a surprising
  reading is which build produced it.

### Fixed

- The bundled skill teaches the unmeasured-port finding that 1.0.2 introduced,
  and no longer tells a Windows user to re-run elevated to reveal a port's
  capability - the registers are not published there at any privilege, so the
  second run returns the same dashes.

## [1.0.2] - 2026-08-07

Corrects a false diagnosis. On Windows, a drive that was doing everything right
could be reported as faulty, with instructions to reseat hardware that is
soldered down and to check a cable that does not exist.

### Fixed

- A PCIe link is no longer called a fault when only one end of it was measured.
  Where the port's capability could not be read, the tool took the DEVICE's own
  maximum as the port's, so any drive faster than its socket looked like a
  negotiation failure. Reported from a Gen5 NVMe drive in the CPU-attached Gen4
  x4 M.2 socket of a Raptor Lake board: it was running exactly as fast as that
  socket allows, and was told to reseat the card, check the riser and cabling,
  and look for a BIOS slot-speed override. Such a shortfall is now reported as
  real but unattributable, in yellow, and the remedy no longer names a cause.
- Windows reports the driver behind each controller. It was never asked for, so
  every controller showed "-" where Linux shows the bound kernel module. The
  column now reads stornvme, storahci or iaStorVD, which is the same thing the
  Linux column means.

### Added

- Controllers carry the name of the port above them, and the structured output
  exposes it as `upstream_name`. Windows publishes no link registers for PCIe
  bridges - measured on one board: 8 bridges, none with a link speed, and no
  registry, WMI or user-mode API that has them - but it does name the port, and
  a name like "Intel(R) PCIe RC 060 (x4) G4" answers what the missing registers
  left open. It is quoted, never parsed into a capability: it comes from a
  driver package rather than from the hardware, and only some vendors put the
  width and generation in it.

## [1.0.1] - 2026-08-07

A maintenance release. It behaves exactly as 1.0.0 does: the only change inside
the package is which line an import sits on, and a sentence in a docstring.

What it really carries is the first green CI run. The outage on release day
cancelled every job before it executed a step, so 1.0.0 shipped on local
evidence alone. Running the matrix afterwards found nine failing
tests across Windows and macOS plus two modules Windows could not import, all
of them in the tests rather than the tool - which is consistent
with 1.0.0 installing and running correctly from PyPI throughout.

### Fixed

- The Linux reader module can be imported on a machine without ``fcntl``. It
  imported that Linux-only module at file scope, so on Windows the module could
  not be imported at all. No released behaviour depended on it: the snapshot
  adapter already imported the reader lazily and only on Linux, so the CLI never
  reached it. It made the module unreachable to tooling, and unimportable by
  anything that walks the package.
- The ``read_text_bounded`` docstring no longer shows a path that only a POSIX
  machine would print.

### Changed

- Tests no longer assume they are running on Linux. Nine failures came from that
  assumption: POSIX file modes, ``os.geteuid``, ``XDG_CONFIG_HOME``,
  ``Path.home()``, path separators in comparisons, and a captured pipe decoded
  with the runner's codepage rather than a stated encoding. The suite now runs on
  Python 3.11 through 3.14 across Linux, macOS and Windows.
- Tests no longer read the developer's own counter history or ``.env``. Both made
  a result depend on the machine it ran on, and one of them silently supplied the
  secret that another test needed in order to prove anything at all.

## [1.0.0] - 2026-08-06

First release. Everything below is what it does.

### Added

#### What it reports

- Disks grouped by the controller they hang off, with every interface link
  graded against what both ends of it can do: the port, the drive, and what the
  two negotiated, shown as three numbers rather than one verdict.
- SMART wear, temperature and error counters, judged against the thresholds each
  drive publishes for itself rather than against a fixed rule. An attribute the
  drive's own maker marks as failing is a critical finding.
- A mainboard slot view: every PCIe port, its capability, what occupies it, what
  that occupant needs, and which ports are free.
- The mainboard named from DMI, which is what makes the placement advice
  actionable.
- Controller oversubscription, where the drives on a controller together want
  more than its uplink carries.
- Findings carry the measurement that produced them and what to do about it, and
  severity is graded against what the machine can actually give: a link below a
  device's own maximum is a fault only when something better is available.

#### How you drive it

- A bare `lsdsk` renders the whole machine on one page; `topology`,
  `controllers`, `disks`, `health`, `smart`, `findings`, `slots` and `trend` are
  each one section of it, under the same names as the interactive pages.
- An interactive view (`lsdsk tui`) keyed like the `*top` family.
- `lsdsk snapshot` captures the raw reading, and `--replay` renders a capture
  from any machine through the identical decode and diagnosis path. `snapshot`
  itself always captures the machine it runs on and refuses `--replay` rather
  than silently ignoring it.
- `--format json` emits a validated envelope carrying `ok`, `command`, `data`
  and `skipped`, on every command that produces data, so one reader handles them
  all without branching on shape. `tui`, `fail` and `logdemo` have no structured
  mode, having no data to structure.
- Exit codes a caller can branch on, documented and fixed: `0` and `1` from the
  findings of a reporting command, `2` for a usage error, `13`, `22` and `78`
  for the three ways a run cannot proceed.

#### Counter history

- Counter history, so an error count becomes a rate. A drive keeps the lifetime
  total in its own non-volatile table and never keeps the past, so the total
  cannot say whether a fault is live or years old. `lsdsk trend` says which,
  every finding carries the measurement, and severity follows: a counter proved
  to be rising is escalated and one proved to be quiet is stood down.
- Rates are measured per power-on hour of the drive itself, so they survive
  clock steps, timezones and a machine that spends most of its time off.
- Silence counts as evidence only where the drive's own lifetime rate says
  errors should have appeared, so a slow trickle over a short span reports that
  it cannot tell rather than implying the drive is fine.
- `lsdsk record` stores one reading and prints nothing, for a timer; with
  `--format json` it reports what it stored. Any ordinary run also records once
  the drives' clocks have moved on, so the history feeds itself.
- The store is written atomically, is owner-only, and is capped per drive. A
  store that cannot be read is left exactly as it is rather than replaced,
  because unlike a capture it cannot be rebuilt from the hardware.
- As root the store lives at `/var/lib/lsdsk/history.json`, and per-user
  otherwise. Reading the counters needs root, so on a server the root path is
  the one that fills.

#### Platforms and safety

- Linux and Windows, issuing no subprocesses and making no network requests.
  Windows support is `ctypes` alone: no WMI, no PowerShell.
- Anywhere else runs `--replay` against a capture taken on a machine it supports.
- Text a device reports is stripped of control characters where it is decoded,
  so a model number cannot carry escape sequences into a terminal.
- A capture handed to `--replay` and a store handed to `--history-file` are
  refused above 64 MB, from the directory entry rather than after reading, so
  pointing either at a disk image fails immediately instead of spending the
  memory first. The largest capture from a real machine is 148 KB for 19 drives.
- Secrets in configuration are redacted by what a key's name means rather than
  by how it was spelled, in the human and the structured view alike.
- It runs unprivileged and says what that costs, naming the columns it could not
  read instead of reporting them as zero.

#### Configuration

- `[thresholds]`, `[display]` and `[history]` sections: every value the tool
  judges or lays out by is a documented configuration key, deployed with
  `lsdsk config-deploy` and overridable for one run with `--set`.
  Specification-fixed values, such as register offsets and the Kelvin offset,
  stay fixed.
- Every documented key is covered by a test that sets it two ways and requires
  the tool to behave two ways, so a key that reads as configurable is.

#### For agents

- A Claude Code skill teaching how to read the output and what to do about each
  finding, including that a large error count ranks nothing until the rate is
  known, and that a normal run records one reading.

[1.0.3]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.3
[1.0.2]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.2
[1.0.1]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.1
[1.0.0]: https://github.com/bitranox/lsdsk/releases/tag/v1.0.0
