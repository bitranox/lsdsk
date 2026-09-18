# The eight pages

**English** | [Deutsch](de/PAGES.md)

One page per topic, in the order the number keys reach them. Each page
corresponds to a subcommand that prints the same view as text, so `4` and
`lsdsk health` are one thing under one name. Back to the [README](README.md).

The interactive view is operated the way the `*top` family is: `1` to `8` or the
matching function key switch page, and `q` quits; the footer lists those, so
there is nothing to memorise. `left`, `right`, `tab` and `shift+tab` also step
between pages, and `r` rescans, without appearing in the footer.
The pages carry the same names as the commands, in the same order, so `4` and
`lsdsk health` are the same view, drawing the same columns.
Each page stands alone and shows every disk, so nothing has to be selected to
read one.

Six of the eight pages also carry a cursor - topology, controllers, disks,
health, slots and trend - which `up` and `down` move. A panel under the table
answers for whatever the cursor is on: every value the row had no column for,
then the findings, with their reasoning and their remedy. `i` hides the
panel and gives the table the whole screen; `shift+up` and `shift+down` scroll
inside it, and are offered only when the record is taller than the panel is.
SMART and findings carry no cursor, scroll as a page, and the panel reads
`Nothing selected.` there.

On the topology page, `d` switches how much detail the PCI fabric is drawn in,
the same setting `--tree-density` sets for every view. `,` and `.` scroll the
WWN where that is needed.

## 1 Topology

![Topology](docs/screenshots/1-topology.png)

What is wrong comes first, then the machine itself: each controller with the link it negotiated,
and the drives hanging under it.

## 2 Controllers

![Controllers](docs/screenshots/2-controllers.png)

One row per controller: driver and firmware, running against capable, how many ports it has and
how many are free, and what the drives on it would pull together.

## 3 Disks

![Disks](docs/screenshots/3-disks.png)

One row per drive, with the identity you need to order a replacement: model, WWN, serial and
firmware, then size, bus and the three speeds. `port` is what the seat can give, `disk` what the
drive can do, `link` what the two of them agreed on.
`size` gives the capacity in decimal and in powers of two.

## 4 Health

![Health](docs/screenshots/4-health.png)

Wear, temperature, power-on hours and bytes written, beside the counters that decide whether a
drive is dying or merely badly cabled: reallocated, pending, uncorrectable, interface CRC and
media errors.

## 5 SMART

![SMART attributes](docs/screenshots/5-smart.png)

Every attribute of every drive, with value, worst and threshold beside the raw number, because a
raw count on its own says little.
NVMe drives publish a fixed health log instead of an attribute table, and the page shows that.

## 6 Findings

![Findings](docs/screenshots/6-findings.png)

Each finding with its reasoning underneath and a recommended remedy: what was measured, what it
means, and what to do about it.

## 7 Slots

![Slots](docs/screenshots/7-slots.png)

Every PCIe port: what it can deliver, what it is running, what occupies it, what that occupant
needs, and a verdict. `FREE` is an empty port, `full` means the occupant uses what the port
offers, and a spare figure is the bandwidth nobody is currently using.

## 8 Trend

![Counter trends](docs/screenshots/8-trend.png)

The counters over time rather than as a single value: the change since the last reading, the
span it was measured over, a rate per hour, and a verdict. Two drives here read `rising`. One
reads `no new in 16h, 235 were due`, a counter that has stopped climbing.
The rest say `too soon to say`, until enough time has passed between two readings.
