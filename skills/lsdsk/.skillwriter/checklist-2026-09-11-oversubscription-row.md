# skill-writer review: the oversubscription row routes to its section

The finding table's row for an oversubscribed controller offered an HBA as an
equal remedy, stated the uplink figure as fact, and did not point to the section
below it. That section says to check the uplink figure first and treats an HBA as
the last resort, so a reader scanning the table acted on the row alone.

## Route

A row-scoped quote-back that accepts only a verbatim quote from that one table
row, or the single word NONE. An agent cannot quote text the row does not
contain, whatever it already knows, and it may not quote the section below even
though the section answers every question: the defect is that the row and its
section disagree, so the section must not answer for the row.

The question the backlog item raised first, whether "carries" still read as the
negotiated link once the ceiling became what a link can carry, settles on
reading: "carries" reads as capacity, which is now what the ceiling is. The live
defect is the one the section exposed.

## RED

- Do column: "Spread them over more controllers, or fit a wider-uplink HBA. A
  free port here is not free"
- A pointer to a section: NONE
- A caveat that the uplink figure may not be a measurement: NONE
- Which remedy to try first: NONE

## GREEN

The same four questions, with the same model and wording, against the edited
file:

- Do column: "Check that figure before relaying it, and move a drive to a
  controller already fitted before any HBA. See below"
- A pointer to a section: "See below"
- A caveat: "Check that figure before relaying it"
- Which remedy first: "move a drive to a controller already fitted before any
  HBA"

Every NONE is now a verbatim quote from the row, and the one answer that was
already a quote still is.

## Declared

- The row drops "A free port here is not free". The section it now points to
  carries it: "A free port on an oversubscribed controller is not free."
- No `Skill gaps` list: the quote-back format admits only a quote or NONE.

## Checklist

- [x] RED recorded before the row changed
- [x] The route replacing a behavioural arm is stated, with why the section may not answer for the row
- [x] GREEN asked the same questions, with the same model and wording, as RED
- [x] Every NONE became a verbatim quote of the row
- [x] Nothing the RED answers produced is lost; the dropped clause is in the section the row points to, checked by string match
- [x] The table keeps four cells per row, realigned by the formatter
- [x] Frontmatter untouched, so no routing keyword moved
- [x] No hostnames, addresses or machine paths added
