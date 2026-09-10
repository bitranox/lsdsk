# skill-writer review: documentation can refute a finding, not only supplement it

GREEN and REFACTOR for the RED recorded in
`red-2026-09-10-documentation-refutes-a-finding.md`. The skill taught published
documentation as a SUPPLEMENT for what lsdsk cannot measure and never as
something that can REFUTE a finding it did make, and it carried no device class
for a chipset used as a PCIe switch on an add-in card, whose integrated
functions publish link registers that describe no link.

## RED, carried forward

The baseline failed three ways on a real capture: it confirmed a 0.25 GB/s
uplink ceiling as measured, it looked nothing up in one tool call although the
skill says lane sharing is worth looking up every time, and it invented a claim
that the controller is an onboard chipset function rather than a card, which
sends a reader shopping for an HBA. Full evidence in the RED artifact.

## The baseline is not contaminated

`redcheck --corpus-cascade` reports STRONG inherited coverage over 1109
documents, naming this repo's own `CLAUDE.md` at 30 percent. Adjudicated against
the sources rather than the matched text, that firing is a vocabulary artifact:
its shared terms are `ahci`, `pcie`, `controller`, `drive` and `slot`, which a
corpus of storage documents shares by construction. No named document contains
`integrated function`, `expansion card`, `PCIe switch`, `Gen1 x1` or
`placeholder`, and every `floor` hit is a different sense of the word - a
dependency floor, a contrast floor, a noise floor. The lesson under test is in
none of them.

## GREEN

Four edits, and the same scenario re-run against the edited text.

| Edit                                      | Closes                                                         |
|-------------------------------------------|----------------------------------------------------------------|
| The arbitration in "Keep the two apart"   | A specification or measurement can refute, not only supplement |
| A new `Controller oversubscribed` section | The device class, and the two remedies had no priority         |
| The card-versus-onboard limit, twice      | A row does not say which, so the remedy text is a template     |
| `running`, `capable` and `load` defined   | The controller table's columns were never defined              |

The arbitration has a DIRECTION, which is what makes it a test rather than a
licence to disbelieve the tool. A ceiling cannot be exceeded, so a measured
throughput above one refutes it outright; a specification BELOW what lsdsk
reports settles nothing, because that is what a downtrained or shared link looks
like.

The discriminator for the device class is a SECOND integrated function on the
same silicon publishing the identical floor, not the floor value alone, because
a genuinely dead link reads the same.

`load` is read off `attached_demand_gbytes` in `domain/diagnostics.py`: it sums
each attached disk's NEGOTIATED link bandwidth, so it is a capability total and
not traffic.

The GREEN run reversed all three baseline failures. It called the figure wrong
rather than low, citing `running` equal to `capable` at the floor and the USB
function on the same chip at the identical `Gen1 x1` beside NVMe ports at Gen3,
Gen4 and Gen5. It went offsite and found the card class documented at a PCIe 4.0
x4 uplink. It reached the opposite conclusion from the baseline on the card
question, from the board generation: a 500 Series board cannot own a 600 Series
controller. It also put the free-controller move ahead of the HBA, and told the
user to measure before spending.

Diffed against RED in both directions. Nothing the baseline produced is missing
from GREEN: the baseline's only outputs were the three failures, and it produced
no correct finding that the edited text displaced.

## REFACTOR

GREEN was asked for its gaps and reported four, plus NONE on the two structural
checks it applied directly.

| Gap GREEN reported                                                                                           | Outcome                                                                                                                                                             |
|--------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| "Read the ports sharing the controller's upstream" does not say how, when the two tables' addresses differ   | CLOSED. `slots` rows are PORTS; `slots --format json` carries `occupant_address`, which IS the controllers-table address. Join on it                                |
| Told the user a controller with zero drives is somewhere to move a drive, which the count does not establish | CLOSED. Spare bandwidth is not a reachable port; `ports` and `free` reading `-` is unknown, not zero                                                                |
| "Ask for one throughput figure" is unreachable in a single-shot reply                                        | CLOSED. Answer on the other two readings, give the command, and say which answer each result would give                                                             |
| No spec page for the specific card, so no exact uplink figure for it                                         | DECLINED. Inherent, and the skill already says the real uplink is on the card's spec page. The run reported the mechanism without pinning a number to that hardware |

The first gap was a hole this change introduced: the instruction was written
against a topology the printed table does not show, so a reader had to supply
PCI convention the document never states.

## Quote-back verification

Each contested point re-asked against the edited file, answerable only by a
verbatim quote or the word NONE. Four questions, four quotes, no NONE:

- [x] Matching a controller row to a slot row: quotes the `occupant_address` join
- [x] Zero drives as a destination: quotes the spare-bandwidth-is-not-a-port rule
- [x] No measurement available in this exchange: quotes the fallback clause
- [x] A published figure BELOW the reading: quotes the clause saying it settles nothing

## Checklist

- [x] RED recorded and failed before any text was written
- [x] Inherited-coverage check run and ADJUDICATED against its sources, not its matched terms
- [x] GREEN tested the edited file, in a directory holding nothing else
- [x] Every RED failure reversed, each against a reading in the pasted output
- [x] GREEN diffed against RED in both directions; no baseline result lost
- [x] Every gap GREEN reported is closed or declined with a reason
- [x] Each fix verified by quote-back, not by paraphrase
- [x] `load` verified against the source that computes it, not inferred from the arithmetic
- [x] `occupant_address` verified present in the shipped JSON, not assumed
- [x] Frontmatter untouched, so no routing keyword moved
- [x] No hostnames, addresses, machine paths or third-party hardware identities added
