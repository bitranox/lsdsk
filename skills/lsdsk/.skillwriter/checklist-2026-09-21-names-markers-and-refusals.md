# Checklist - the names, markers and refusals the document owed the package

Three separate ways this document had drifted from the package it describes, all
of the same shape: a sentence that was true when written and that nothing checks.

## The defects this closes

1. **Three constants that cannot be imported.** The Python-API section named
   `WEAR_WARNING_PERCENT`, `WEAR_CRITICAL_PERCENT` and `CRC_ERRORS_SIGNIFICANT`
   as "the shipped figures those rules fall back to". `hasattr` against both
   `lsdsk.domain.diagnostics` and `lsdsk.domain.thresholds` answers False for all
   three, and the CHANGELOG records this branch removing them. A reader following
   the sentence gets `ImportError`.
2. **Two markers the views draw and the document never explained.** `n/a`, which
   says a value cannot exist for that subject, and `<=`, which says a figure is a
   ceiling because only one end of a link was read. The ceiling marker shipped in
   a feature commit with neither a changelog entry nor a line here.
3. **A refusal a caller cannot learn about.** `MissingFileError` is a
   `ConfigurationError` subclass raised for an absent file. Catching the base is
   correct and sufficient, so nothing breaks; a caller wanting to tell "absent"
   from "present and wrong" simply had no way to find out the distinction exists.

## Change

- The constants sentence names `DEFAULT_THRESHOLDS` and its three fields with
  their shipped values, and says to build a `Thresholds` rather than read a field
  off the default.
- The marker section gains a paragraph for the detail panel's two, each with the
  reason it is that marker rather than a dash. The preceding paragraph now says
  "those two" so it still points at the pair it is about.
- The exception paragraph names `MissingFileError` and what catching it buys.
- No finding text, no link figure, no threshold value, no frontmatter moved.

## RED

The lesson under test is recorded in this repo's own CLAUDE.md and its backlog,
both of which a dispatched agent on this machine inherits, so a behavioural arm
would answer from that teaching in both directions. The route taken instead is a
coverage check against the skill FILE, which inherited context cannot reach.

Each guard assembles its expectation FROM THE PACKAGE rather than from a list
kept beside it, so it can find a name nobody wrote down:

- [x] RED `test_every_constant_the_skill_names_is_one_this_package_has` failed
      naming exactly `CRC_ERRORS_SIGNIFICANT`, `WEAR_CRITICAL_PERCENT`,
      `WEAR_WARNING_PERCENT`. GREEN passes.
- [x] RED `test_the_skill_explains_every_marker_a_view_can_draw` failed naming
      `AT_MOST` and `NOT_APPLICABLE`. GREEN passes.
- [x] RED `test_the_skill_names_every_refusal_a_caller_can_distinguish` failed
      naming `MissingFileError`. GREEN passes.

## The guards could have answered otherwise

- [x] The constant guard's first draft ALSO flagged `FREE`, which is not a dead
      import but a cell the slots table prints. That is the guard over-firing, not
      a defect, so it gained a second source: a token passes when the package
      exports it OR the tool prints it over the committed captures. Neither source
      alone can tell a Python name from an output literal.
- [x] Each guard carries a control that fails first if its source came back empty:
      `SCHEMA_VERSION` must be reachable, `FREE` must appear in printed output,
      and the subclass list must be non-empty.

## Quality checks

- [x] Present tense, no session narrative, no scratch paths.
- [x] No address, MAC, hostname or path added.
- [x] Frontmatter untouched; `name` and `description` unchanged.
- [x] ASCII punctuation only.
