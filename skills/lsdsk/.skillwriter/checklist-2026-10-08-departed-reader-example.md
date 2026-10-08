# Checklist - the departed-reader example names a command that really leaves 141

## The change this documents

`lsdsk config --section nosuch` refuses the missing section before it writes anything, so piped
into a reader that has gone it leaves `22`, not the `141` the skill claimed. The paragraph's rule
(a command still writing when the reader leaves exits `141` at that write) is unchanged; its
example was wrong.

## Change

- The example sentence now contrasts the two measured commands: `lsdsk config | head -c 0` leaves
  `141`, `lsdsk config --section nosuch | head -c 0` leaves `22`, with the reason (the refusal
  comes before the first byte).
- No frontmatter changed. Both codes were read from `${PIPESTATUS[0]}` on the current tool.

## RED

- [x] Retrieval probe (text-only agent, weak tier, the paragraph as it stood). Q1, the exit code of
      `lsdsk config --section nosuch | head -c 0`: answered `141`, quoting the old sentence; the
      tool leaves `22`. Q2, a command that leaves `141` because the reader left mid-write: offered
      the same refusing command. Skill gaps reported: none.

## GREEN

- [x] Same probe, new paragraph. Q1: `22`, quoting the new sentence. Q2: `lsdsk config | head -c 0`,
      quoting it.
- [x] Diffed against RED both ways: RED's alternative `141` example (`lsdsk findings ... | head -5`)
      comes from the unchanged first paragraph and is still there; nothing RED answered correctly
      was lost.

## REFACTOR

- [x] GREEN returned no Skill gaps section; RED, asked the same way, reported none. No gap left
      undecided.

## Deployment

- [x] Every test that reads SKILL.md passes (163 across 8 files).
- [x] No address, host or path from a real machine added.
