# ADR 0003: Hints for the PCIe Links No Storage Rule Grades

**Status:** Accepted

## Context

The storage rules grade a controller's link with the drives behind it in mind. Every other PCIe
card - a graphics card, a network card, a switch or bridge carrying either - was drawn in the
topology and judged nowhere, so a card that lost lanes, or that sits in a slot narrower than
itself, went unremarked even where the tree showed both figures side by side.

A link can fall short three ways: it trained on fewer lanes than both ends support (A), it runs at
a lower speed than both ends support at full width (B), or the card can do more than the port it
sits in offers and runs at the port's limit (C).

## Decision

**What is graded.** A and C, each as a HINT, so neither moves the exit code. B is not graded:
graphics cards lower their link speed while idle and retrain under load, so one reading cannot tell
power saving from a fault, and a finding whose usual answer is "this is normal" would fire on
nearly every desktop with a modern graphics card. Width and capability do not move with load. The
running column of the tree still shows a speed-only shortfall.

That width holds still is a measured claim, because AMD's power management levels name a lane
width beside each speed and a card could in principle drop lanes while idle. Read from six AMD
graphics functions of three chips, idle under automatic power management, two of them behind a
dual-GPU card's own switch: none ran narrower than both ends of its link allowed. Every one that ran at x8 sat behind
a port whose own maximum is x8, which is C, not A, and the one chip that publishes its levels
lists x16 at all six. A card that does narrow its link at idle would raise A falsely, so a report
of one is a reason to revisit this, with the level table as the evidence.

**Which links.** Physical readings only, behind the same gate as the storage link rules. A device's
link is paired with its PARENT only where the parent faces downstream (a root port or a switch's
downstream port): those publish the link below them, while every other node publishes the link
above it, so pairing a device with a switch's upstream port would read the wrong link. Skipped: a
storage controller and any device a drive hangs off (their own rules grade them), a device on a root
bus, a link with either end's capability unread (every bridge on Windows), and a link that never
trained, which the tree already draws as `none`. An unread end is never filled in from the other.

**One finding per link.** The functions of one device share its link, so the finding is made once,
at the lowest function address of the device behind the port. A link that lost lanes gets only the
lanes hint: the contact is checked before a move would show what the slot gives.

**Naming.** The subject is the device at the card end of the link. On a dual-GPU card or a riser
that is a switch or bridge chip whose name is not what is printed on the box, so the title adds the
end devices behind it, grouped by name with a count, at most two names and then `and N more`.

**Slot advice for C.** The free-slot search the storage rules use, keyed on a `Seat` (the card's
own link, its port's link, the port's address) so both are held to one set of evidence rules: a
slot is named only where its connector bit was read. Where no connector bit was read, the action
says so rather than implying there is no such slot. Where none would help, it names the port the
card runs in full in. No swap is suggested: the storage swap judges the other card by the demand of
its drives, which a graphics or network card does not have.

## Consequences

- On Windows neither hint appears: the platform publishes no port kind and no link capability for
  a bridge, so no link has two read ends.
- The storage findings are unchanged by the shared search; that was checked byte for byte over
  every committed capture when the search moved.
- A committed capture's hint count can rise when a card in it is capped, which moves the counts a
  document quotes; those are re-quoted from the registered command, never hand-edited.
