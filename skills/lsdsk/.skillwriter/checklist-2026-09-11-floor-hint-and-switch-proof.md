# skill-writer review: the floor hint, and what proves a switch

lsdsk gained a finding for a controller that publishes the PCIe floor beside a
second function at the identical floor and a real link, and it reports that as a
hint instead of an oversubscription warning. The skill had no row for it, never
said the tool recognises the pattern itself or what it reports instead, and gave
the manual check only as an unconditional instruction. Separately, it told a
reader to read ports sharing a bus as one switch, which does not hold on a root
complex.

## Route

A quote-back that accepts only a verbatim quote from the file or the single word
NONE, as for this skill's other changes: an agent cannot quote a sentence the
file does not contain, whatever it already knows. The bus point was raised after
that quote-back had run, so its RED is a text check of the file, while its GREEN
uses the same quote-back format as the rest.

## The behaviour was verified before the text describing it

- The rule converts the reported case: replayed as JSON, the controller carries a
  hint titled "publishes the PCIe floor", no finding is titled "oversubscribed",
  and the capture's three other warnings are unchanged.
- A switch is proven by a slot record whose occupant sits on the controller's
  bus. Both platform builders create a slot record only for a bridge and point it
  at that bridge's secondary bus, so no record points into a root bus. Ports on a
  root bus keep the warning, pinned by a test that fails when the guard is removed.

## RED

Quote-back, before the edit:

- A row for a controller publishing the PCIe floor: the only row offered was
  "Controller oversubscribed", which is a different finding.
- A sentence saying lsdsk recognises the register default itself: NONE.
- A sentence saying what it reports instead: NONE.
- When the reader still checks by hand: only the unconditional "Check the uplink
  figure against something outside the tool before you relay it."

Text check for the bus point: the sentence saying ports on one switch share a bus
and the bus prefix is the group to read together is present unqualified, and
nothing in the file mentions a root bus, a root complex, a bridge above a bus or
independent slots.

## GREEN

The same questions, with the same model and wording, against the edited file,
plus the bus question in the quote-back format:

- The row: "| Publishes the PCIe floor as its link | An integrated function's
  register default, so not a ceiling | Check the card's or board's specification
  for the real uplink; replace nothing on this figure. See below |"
- Recognition: "lsdsk recognises the second and third readings itself when the
  controller sits behind a switch ..."
- What it reports instead: "It then reports the controller as publishing the PCIe
  floor, a hint, instead of calling it oversubscribed."
- When to check by hand: "Where it cannot tell (no slot data, every link on the
  switch at the floor, or a controller that sits on a root port rather than behind
  a switch) it still raises the oversubscription warning, and the check above is
  yours to make."
- The bus point: "... but only when another row's occupant sits on that bus,
  because that row is the bridge the bus hangs behind. Ports directly on a root
  bus share a number too and are still independent slots."

Diffed against RED in both directions: every NONE is now a quote, the manual-check
answer moved from an unconditional instruction to the sentence naming when the
tool cannot tell, and that unconditional instruction is still in the file. Nothing
the baseline produced is lost.

## Declared

- The bus point's RED is a text check while its GREEN is a quote-back, because the
  point arose after the RED quote-back had run.
- No `Skill gaps` list: the quote-back format admits only a quote or NONE.

## Checklist

- [x] RED recorded before the edit, for every point
- [x] The behaviour the new text describes verified against the code and a replay first
- [x] GREEN asked RED's questions with the same model and wording
- [x] Every NONE became a verbatim quote
- [x] GREEN diffed against RED in both directions; nothing lost
- [x] The new table row keeps four cells, realigned by the formatter
- [x] Frontmatter untouched, so no routing keyword moved
- [x] No hostnames, addresses or machine paths added; the `0000:08:` bus example was already in the file
