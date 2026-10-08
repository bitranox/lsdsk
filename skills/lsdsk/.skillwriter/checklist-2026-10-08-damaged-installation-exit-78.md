# Checklist - a damaged installation leaves 78

## The change this documents

lsdsk's own shipped configuration (`defaultconfig.toml` and its `defaultconfig.d/`) is now read
before the configuration load. Missing, unreadable or corrupt, it refuses at `78` with one sentence
naming the installation as damaged. Before, a missing file left `2` and an unreadable one `13`, both
read as the caller's mistake.

## Change

- The `78` row of the exit-code table gains one cause: a damaged installation, whose own shipped
  configuration is missing, unreadable or corrupt, with the action (reinstall the tool).
- No frontmatter changed. The three codes were measured end to end on damaged copies of the package
  (`tests/test_shipped_config_damaged.py` holds them).

## RED

- [x] Retrieval probe (text-only agent, weak tier, the `2`, `13`, `70` and `78` rows as they stood),
      scenario: an antivirus quarantined the package's shipped `defaultconfig.toml`. Q1 answered `78`
      by inference from "A configuration file this tool cannot load". Skill gaps reported: the table
      does not say whether a SHIPPED file counts, and it had to choose between `13` and `78`. The
      `70` row's "an unhandled `OSError` keeps its own code" is what the tool actually did before
      this change (`2`), so the guess matched neither the old tool nor the text.

## GREEN

- [x] Same probe, new row. Q1: `78`, quoting "a damaged installation, whose own shipped
      configuration is missing, unreadable or corrupt - reinstall the tool". Q2: reinstall/repair,
      same quote. Skill gaps: none.
- [x] Diffed against RED both ways: both answer `78`; GREEN removes the two guesses RED reported and
      adds the routing action. Nothing RED answered was lost.

## REFACTOR

- [x] Both RED gaps (shipped vs user file; `13` vs `78`) are closed by the new clause. No gap left
      undecided.

## Deployment

- [x] Every test that reads SKILL.md passes.
- [x] No address, host or path from a real machine added.
