# Checklist - the `none` hop symbol

## The change this documents

A link read at width x0 now draws `none` in the topology's running column, with
the legend line `none = no link trained`, where it used to draw `Gen1x0` (the
reset speed beside a zero width) or `-` (which says nobody read the register).

## Change

- The hop-symbol list gains a third bullet: what `none` means, that it is a
  reading like `legacy` and not a gap like `-`, that it calls for no action on its
  own, and how the row tells an empty slot from a built-in function.
- The list's lead sentence says "None of them is a fault" instead of "Neither".
- No frontmatter, no field list, no exit code moved.

## RED

- [x] Retrieval probe (text-only agent, the hop-symbol passage as it stood) with a
      real `none` row and its legend. Q1 (is the port broken, reseat it?) and Q3
      (is anything plugged in?) were answered NONE; Q2 (same as `-`?) could only
      say the text does not define `none`. Its gaps named the undefined symbol.

## GREEN

- [x] Same probe on the new passage: Q1 quoted the new bullet and answered "no
      fault, nothing to reseat"; Q2 answered "a successful reading, not a failure
      to read". Q3 was still NONE: two causes named, no way to tell them apart.

## REFACTOR

- [x] Gap closed: the bullet now says the row decides it - a row tagged
      `root port` or `switch port` with nothing beneath it is a slot, any other row
      a built-in function. The tags quoted are the ones `theme.pci_tag` prints; a
      first draft quoted `downstream port`, which the tree never prints.
- [x] Quote-back on Q3 (a `root port` row with nothing beneath it, and a QPI
      function, both reading `none`): the answer was a direct quote of the new
      sentence, and each address was classified correctly (empty slot, built-in
      function).
- [x] Gaps declined: the probe saw an excerpt, so it could not read the tree glyphs
      or find a QPI example; the full skill documents the glyphs in its topology
      section, and the rule classifies by exclusion on purpose.

## Deployment

- [x] No address, host or path from a real machine beyond the reserved-looking
      PCI addresses of a committed capture's sample rows.
