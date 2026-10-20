# Checklist - the skill names the install-shortcut command and --install-uv

## The change this documents

The CLI gained the `install-shortcut` command (create the desktop launcher deliberately) with an
`--install-uv` option. The usage skill lists every command and names every option the CLI offers,
so one line is added to the "Running it" block naming both.

## Change

- `skills/lsdsk/SKILL.md`, "Running it": a new example line
  `uvx lsdsk install-shortcut    # drop a desktop shortcut that launches lsdsk; --install-uv installs uv first if it is missing`,
  placed with the other action commands between `config-generate-examples` and `--replay`.

## RED

- [x] The shipped package-agreement gate was the failing test, first: with the command registered
      and the skill unchanged, `tests/test_the_skill_agrees_with_the_package.py::test_the_skill_names_every_option_the_cli_offers`
      failed with `accepted by the CLI and never named in the skill: ['--install-uv']`. This is a
      reference-doc sync: the test exists precisely so a new option cannot ship unnamed, and it was
      red because the option was unnamed.

## GREEN

- [x] With the line added, the whole `test_the_skill_agrees_with_the_package.py` suite passes
      (12 passed), including the command-named and option-named checks and the no-invented-option
      check (`--install-uv` is a real option of `install-shortcut`).
- [x] `test_skill_describes_the_fabric_view` and `test_skill_quotes_output_the_tool_produces` stay
      green: the edit adds one command line and quotes no tool output.

## Gaps the runs reported, and their decision

- Closed: the command and its option are now discoverable in the usage skill.
- Declined, out of scope here: the interactive desktop-shortcut OFFER, the consent/uv-install flow
  prose, and the macOS/Windows shortcut behaviour are documented when those pieces ship, in the
  same skill's own later passes; this change only closes the gate the registered command opened.

## Quality

- [x] No frontmatter change.
- [x] No address, hostname or path added; the line uses `uvx lsdsk install-shortcut`, generic.
