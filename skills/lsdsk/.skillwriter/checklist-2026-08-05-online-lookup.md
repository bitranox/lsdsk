# skill-writer review: closing lsdsk's limits with a manual lookup

## What changed

- A section stating that lsdsk's no-network limit is the TOOL's, not the reader's, with a
  table of the questions it leaves open and where each is published: board spec and
  manual, HBA or RAID controller datasheet, drive spec sheet.
- The search keys lsdsk already prints are named: board from DMI in the banner, controller
  model in `controllers`, drive model in `disks`.
- Lane sharing called out as the lookup worth doing every time, because it explains a whole
  class of findings the tool can see the result of but not the cause.
- A rule to keep measured and looked-up figures apart, with the source cited.

## RED

- [x] A first baseline PASSED and was DISCARDED as contaminated: the scenario ended with
      "Use any tool you have available if it helps", which is the instruction under test.
- [x] Re-run with that invitation removed and mild time pressure ("change window at 4pm"):
      the agent made **0 tool calls**, answered "Spare SATA port count is not answerable
      from this tool", and stopped. The board's port count is published on the vendor's
      own spec page.
- [x] The same run reported unprompted that SAS-versus-SATA incompatibility was "my domain
      knowledge, not something the skill text states".

## GREEN

- [x] Same scenario against the new text: **6 tool calls**. Found the Broadcom product
      brief, caught that the card is rated PCIe Gen4 x8 while running Gen3 x8, cited both
      sources, and kept measured figures separate from looked-up ones.
- [x] Reported honestly that supermicro.com answered 403 and that it had fallen back to a
      retailer listing "one hop removed from the primary source".
- [x] Both dispatches required a `Skill gaps` section; both returned one.

## REFACTOR

- [x] GREEN gap closed: the text did not say what lsdsk's SAS port count counts. Verified
      on live hardware first, rather than accepting the run's explanation: the 9500-16i
      publishes 21 phys with NO expander present, on a sixteen-lane card, while the sibling
      SAS3008 publishes exactly 8. The run had inferred an expander, which the machine
      disproves. The section now says the count is phys rather than connectors and must be
      checked against the card's specification.
- [x] GREEN gap closed: vendor sites that refuse an automated fetch. The rule is to name
      the source actually used and hand over the URL, never to substitute silently.
- [x] Declined, with reason: what a full findings run would have shown, and the exact
      convention behind one controller reporting a dash while another reports numbers
      (properties of a scenario that supplied only the `controllers` table; the skill
      already explains the dash).
- [x] GREEN diffed against RED in both directions. RED produced only a refusal to answer;
      nothing of value is absent from GREEN.

## Quality

- [x] No session narrative, operator instructions, or scratch paths in the skill or here.
- [x] Reserved documentation values only; the hostnames in the scenarios are invented and
      no address or path from a real machine was added.
- [x] Frontmatter untouched: two fields, description remains triggers-only.
- [x] `make test` green after the change.
