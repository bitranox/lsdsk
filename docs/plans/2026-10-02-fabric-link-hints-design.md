# Hints for non-storage PCIe links - design

Every PCIe link the machine carries is graded, storage or not, and a link that
loses lanes or is capped by its slot raises a HINT. Storage controllers keep the
rule they have.

## Decisions

| Question            | Decision                                                                                 |
|---------------------|------------------------------------------------------------------------------------------|
| What counts         | A: lanes lost. C: the slot caps the card. Not B: a speed-only shortfall                  |
| Severity            | HINT for both; a hint leaves the exit code at 0                                          |
| Subject             | Function 0 of the card-end device; the title says what it carries behind a bridge/switch |
| Slot advice for C   | Name a free slot with a read connector bit that would carry more; no swap suggestions    |
| A and C on one link | A only: a link that lost lanes gets the contact advice first                             |

Why not B: graphics cards lower their link SPEED while idle and retrain under
load, so one reading cannot tell power saving from a fault. A hint whose usual
answer is "this is normal" would fire on nearly every desktop with a modern
GPU. Width and capability do not change with load, so A and C are stable
readings. The running column of the tree still shows a speed-only shortfall.

## Which links are graded

- Physical readings only, behind the same `readings_are_physical` gate as the
  storage link rules.
- One finding per LINK: the port (a parent that is not a root bus) and the
  device behind it. The functions of one device share its link, so the finding
  is made once, at function 0.
- Skipped: a storage controller and any device a drive hangs off (their own
  rules grade them), a device on a root bus (no port above it), and a link with
  either end's capability unread - every bridge on Windows. An unread end is
  never filled in from the other.

## How it reads

A - lanes lost, running narrower than both ends support:

```text
 ~  0000:01:00.0  Intel 41210 [Lanai] bridge, carrying 2x NetXtreme II BCM5706, runs on fewer lanes than both ends support
    Running Gen1x4 (1.00 GB/s) where the card and the port at 0000:00:02.0 both support Gen1x8 (2.00 GB/s): 4 lanes did not train.
    Reseat the card and check the slot and any riser; a lane that does not train is usually a contact.
```

C - the card can do more than its port offers, and runs at the port's limit:

```text
 ~  0000:04:00.0  PLX PEX 8747 switch, carrying 2x Radeon HD 7990/8990, is capped by its slot
    The card can do Gen3x16 (15.75 GB/s); the port at 0000:00:02.2 offers Gen3x8 (7.88 GB/s), so it runs there.
    Move it to the free slot at 0000:00:03.0 (Gen3x16). Check the slot is long enough first.
```

Where no free slot would carry more, the action says so. Where the connector
bits were not read (an unprivileged run), it says that was not readable rather
than implying there is no such slot.

The `carrying` clause lists the end devices (not bridges) below the card-end
device, grouped by name with a count, at most two names then `and N more`. A
plain card carries no clause.

The exact sentences above are the design's intent; the implementation keeps
the figures in the repo's one spelling (`format_pcie_sentence`).

## Expected on the committed captures

| Capture                    | Findings                                                       |
|----------------------------|----------------------------------------------------------------|
| linux-usb-ehci             | A on the Lanai bridge; C on the PLX switch naming 0000:00:03.0 |
| linux-sas-hba (and -later) | C on the Pitcairn card, x16 in an x8 port, no free slot helps  |
| linux-nvme-board           | C on the Hawaii card, x16 in an x8 port, no free slot helps    |
| linux-minimal              | C on the RTX 4070 Ti, a Gen4 card in a Gen3 port               |
| windows-ahci, -usb-uas     | none: a VM, and a platform that publishes no bridge capability |

## Where it sits

- `domain/fabric_links.py`: `diagnose_fabric_links(inventory)`, called from
  `diagnose()`. Links are enumerated from `inventory.pci_tree`.
- The free-slot search (`_best_slot`, `_free_slot_for`) takes a `Seat` value -
  the card's own capability, the port's capability, the port's address - which
  a storage controller and a fabric link both produce. One search, one set of
  evidence rules; the storage findings must come out identical.
- Rendering adds nothing: the subject is the card-end address, so the existing
  severity index marks that tree row and the findings section lists it.

## Tests

- An exact table of (subject, kind) per committed capture.
- Hand-built cases, one per guard: unread port end, root-bus device, storage
  controller, multi-function card, A over C, speed-only silent, free slot named
  only with a read connector bit (and the unprivileged wording), the `and N
  more` cap. Each guard mutated once to prove its test can fail.
- The storage findings over every capture, compared before and after the
  `Seat` refactor.

## Docs

FINDINGS.md "What it finds" plus its German twin, the SKILL findings table
(through the skill writer), and CHANGELOG under 1.6.0.
