# Checklist - the register-default flag in the JSON envelope

## The change this documents

`--format json` now says which links the pages draw as `Gen1x1*`: each controller carries
`link_is_register_default` and each slot `occupant_link_is_register_default`. The skill said a
script reading `link` "must not treat that as a ceiling" and gave it nothing to tell such a link
from a real slow one, and its enumeration of a controller's fields omitted the new key.

## Change

- Register-default paragraph: one added sentence naming both flags, saying they are true exactly
  where the pages draw `Gen1x1*`, and that a script skips on the flag, not on the figure.
- Controller field sentence: `link_is_register_default` added after `link`.
- No frontmatter changed.

## RED

- [x] Retrieval probe (inert probe agent, the two passages as they stood): asked which JSON field
      marks a register-default link, it answered `NONE` and said the skip "cannot be implemented
      until the JSON carries a field for the register default".
- [x] Text check: new test `test_the_skill_enumerates_the_fields_a_controller_really_carries`
      holds the controller sentence against a real envelope from `windows-chipset-switch`, as the
      disk sentence is held. On the old skill it fails with
      `only in the payload ['link_is_register_default']`.
- [x] Contamination: the flag is new in this change, so no CLAUDE.md, memory fact or doc teaches
      it; the text check is immune to inherited context either way.

## GREEN

- [x] Every test file that reads `skills/lsdsk/SKILL.md` (8 files, 172 tests) passes, the new
      controller-field test included.
- [x] Both flags checked on a real envelope (`windows-chipset-switch`): true for 0000:0e:00.0 and
      0000:10:00.0 on the controllers, and on the slots whose occupant is one of them; false
      elsewhere.
- [x] Retrieval probe on the edited passages: asked for the field, it quoted "The envelope says
      which links those are: a controller carries `link_is_register_default` ..."; asked whether
      skipping on 2.5 GT/s x1 is the same, it quoted "so a script skips on that flag rather than on
      the figure, which a real slow link shares" and rejected the proposal.

## REFACTOR

- [x] Skill gaps the probe reported, each decided:
      - "the flag's JSON type is not stated" - declined, "true exactly where" names the value and
        the field sentence lists it beside the other keys every controller carries;
      - "the floor hint is not in the excerpt" - declined, the probe was shown two passages only;
        the full skill carries the hint below the paragraph;
      - "could the tree and the table disagree with the flag" - declined, the paragraph says all
        views draw the mark from one decision and the flag is true exactly where they do;
      - "what of an unread link" - declined, out of scope for this change;
      - "does `link` carry a bandwidth field" - declined, the envelope's link model is outside
        this change.

## Quality

- [x] No address, hostname or path from a real machine added; the PCI addresses are the committed
      fixture's.
- [x] No session narrative.
