# RED baseline: can a reader of this skill catch a finding that is wrong?

Status: RED recorded, GREEN not written. The skill is unchanged.

## The claim under test

The skill teaches published documentation as a SUPPLEMENT for what lsdsk cannot measure
(form factor, lane-sharing footnotes, rated endurance). It does not teach that a published
specification or an independent review can REFUTE a finding lsdsk did make. It also carries
no device class for a chipset used as a PCIe switch on an add-in card, whose integrated
functions publish link registers that describe no link.

## Method

One general-purpose subagent (sonnet), given the skill to read and nothing else, answered a
user asking whether an lsdsk finding was right before buying hardware. The input was the
banner, the problems block and the controllers table of a real capture, including:

    0000:11:00.0  AMD 600 Series Chipset SATA Controller  storahci  1.0 x1  1.0 x1  3  1.80 GB/s
    -> Spread the drives across controllers, or replace this card: it is already at its own
       maximum, so a wider slot would not help.

on a board whose own chipset SATA is a 500 Series part. Ground truth, established from the
capture, the chipset specification and a published review of that card: the 0.25 GB/s ceiling
is not the data path. The review measured about 1.7 GB/s across four SATA drives on the same
card model.

## Outcome: the baseline fails, in three ways

1. **It confirmed the wrong figure as measured.** "The arithmetic is sound ... the uplink is
   oversubscribed roughly 7:1." The published link value was never questioned.
2. **It looked nothing up.** One tool call, to read the skill. The skill's own instruction that
   lane sharing is worth looking up every time did not produce a lookup.
3. **It added a new wrong claim.** It called the controller "one of the motherboard chipset's
   own SATA functions ... not a separate card you can pull out". The controller is on an
   add-in card. A reader acting on that buys an HBA they do not need.

The contradiction that opens the case - a 500 Series board carrying a 600 Series chipset SATA
controller - went unremarked.

## Gaps the run reported itself

- "The skill gives no way to tell, from a controller row, whether it names a discrete add-in
  card (physically replaceable) or a motherboard chipset function ... a reader following the
  skill alone would take 'replace this card' at face value and shop for the wrong thing."
- The controllers table's `running` / `capable` / `load` columns are never defined; that `load`
  is a capability sum rather than a measurement had to be inferred.
- "Spread across controllers" and "fit a wider-uplink HBA" are listed with no priority, and
  nothing says to check the same report for a spare controller before recommending hardware.

## The three edits this justifies

1. A published specification or an independent measurement can REFUTE a finding, not only
   supplement it. The section "Keep the two apart when you answer" currently ends the conflict
   the other way: "When they do, lsdsk is describing what is actually there." That holds for a
   negotiated link between two real ends. It does not hold for a register on an integrated
   function, and this case is where it sends a reader wrong.
2. The chipset-as-PCIe-switch device class: an expansion card carrying a desktop chipset
   presents its own downstream ports, and its integrated SATA and USB functions publish the
   PCIe floor while the passed-through ports publish real links. Both integrated functions
   publishing the identical floor value is the discriminator, not the floor value alone.
3. A controller row does not say whether it is an add-in card or an onboard function, so the
   shipped remedy text must not be relayed as if it did.

## Next

GREEN: write the edits, re-run the same scenario against the edited text, require the answer
to question the figure and to look the controller up. Then the REFACTOR pass, the checklist
artifact, and a plugin version bump, since installs only re-fetch on a version change.
