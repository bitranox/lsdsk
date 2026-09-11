# skill-writer review: graded move and swap advice, and the floor hint's vendor check

Two behaviour changes shipped in the tool and the skill described neither. A move
or a swap is now a warning only when the drives on the controller would use the
faster slot and a hint otherwise, under two new titles the finding table had no
row for. The PCIe-floor hint now also requires the controller and the second
device at the floor to carry the vendor identifier of the switch ports in front
of them, and the skill's statement of when lsdsk recognises that pattern omitted
the condition, so a reader holding the warning beside a separate part at the
floor would conclude lsdsk had missed a register default.

## Route

A file-scoped quote-back that accepts only a verbatim quote from
`skills/lsdsk/SKILL.md`, or the single word NONE, over six questions: a row for
each new title, a statement of how a move or swap is graded, the recognition
conditions, what a different maker's device at the floor means, and the JSON
fields that carry the vendors. An agent cannot quote text the file does not
contain, whatever it already knows, so inherited context cannot answer for the
file. Every quote returned was checked by string match against the file, with
whitespace normalised.

## RED

Against the file before the edit:

- Q1 row for "has a faster slot free, though nothing on it needs one today": NONE
- Q2 row for "could swap into a faster slot, though nothing on it needs one today": NONE
- Q3 how a move or swap is graded by the drives: NONE
- Q4 recognition conditions: "lsdsk recognises the second and third readings
  itself when the controller sits behind a switch: its own link reads the floor
  in both columns, another function on that switch reads the identical floor,
  and a third device there has a real link." No vendor condition.
- Q5 a different maker's device at the floor: NONE
- Q6 the JSON fields carrying the vendors: NONE

## GREEN

The same six questions, same model and wording, against the edited file:

- Q1: the new row "Has a faster slot free, though nothing on it needs one today |
  A better slot exists, and the drives on it fit the one it has | Move nothing
  yet. The detail says what the drives pull; the slot is named for when more are
  added"
- Q2: the new row "Could swap into a faster slot, though nothing on it needs one
  today | A swap would help, and the drives on it fit the slot it has | Swap
  nothing yet. The swap is named for when more drives are added"
- Q3: NONE. The rows imply the grading without stating it.
- Q4: the recognition sentence, now with "both carry the vendor identifier of
  the switch ports in front of them"
- Q5: "Where it cannot tell (..., or a device at the floor whose vendor is not
  the switch's) it still raises the oversubscription warning"
- Q6: "`lsdsk slots --format json` carries `vendor` and `occupant_vendor` on
  every port, and a function built into the switch has the same identifier as
  the port in front of it, where a separate part has its own maker's."

## REFACTOR

Q3 stayed NONE, so a paragraph after the finding table states the grading
outright. Only Q3 was asked again:

- Q3: "A faster slot is graded by the drives on the controller. lsdsk reports a
  move or a swap as a warning only when the attached drives would use the
  faster slot, and as a hint that still names the slot when they fit the one
  the controller already has, so an empty controller or a pair of hard disks
  never reads as urgent."

## Declared

- No `Skill gaps` list: the quote-back format admits only a quote or NONE.
- The move row's Means cell changed from "A better slot exists" to "A better
  slot exists, and the drives on it would use it"; its Do cell is unchanged.
- The table was realigned because the new first-column cells are wider; no
  other row's text changed, checked by comparing cell text before and after.
- The README's list of findings says the move advice is graded; the frontmatter
  is untouched, so no routing keyword moved.

## Checklist

- [x] RED recorded before the file changed
- [x] The quote-back route is stated, with why inherited context cannot answer it
- [x] GREEN asked the same questions, with the same model and wording, as RED
- [x] Every quote checked by string match against the file
- [x] The one NONE GREEN left was closed in the text and re-asked alone
- [x] Nothing RED produced is lost: Q4's original conditions are all still in the new sentence
- [x] Frontmatter untouched, so no routing keyword moved
- [x] No hostnames, addresses or machine paths added
