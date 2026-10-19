# Checklist - a `port` reading `>= 6G` is a proven lower bound

## The change this documents

A SATA or SAS drive whose port capability was not read, but whose link runs at the drive's own
maximum, now draws `>= 6G` under `port` with the legend `>= at least: proven by the running link`
(`InterfaceLink.port_floor_gbps`, `src/lsdsk/domain/models.py`). The JSON keeps
`link.port_max_gbps` null, and no rule reads the bound.

## Change

- "Reading a finding": one added passage saying what `>= 6G` means, that the JSON keeps
  `link.port_max_gbps` null so a JSON reader infers the bound from `negotiated_gbps` equal to
  `drive_max_gbps`, and that no finding rests on it.
- The quoted `topology` sample's `/dev/sda` row now carries `>= 6G`, as the tool prints it
  (`tests/test_quoted_output_is_reproduced.py` holds it).

## RED

- [x] A text-only probe given the unchanged section and a JSON excerpt
      (`negotiated_gbps` 6.0, `drive_max_gbps` 6.0, `port_max_gbps` null) reached "at least 6.0"
      by its own inference, and reported the gap itself: "The guide never says that a negotiated
      rate is a lower bound on the port, or how to read a null `port_max_gbps` when the link is at
      full speed." Quote-back for the port statement: no governing line.
- [x] The baseline passed behaviourally, so the evidence is the quote-back, not the answer: a
      capable reader reasoned to the bound, a literal one has no text to act on.

## GREEN

- [x] The same probe on the edited text quotes the governing lines for the bound: "A `port`
      reading `>= 6G` was not read, but the link runs at the drive's own maximum, so the seat
      supports at least that and is not what holds the drive back" and "The JSON keeps
      `link.port_max_gbps` null there, so a JSON reader infers the same bound from
      `negotiated_gbps` equal to `drive_max_gbps`."
- [x] Control (`negotiated_gbps` 3.0 below `drive_max_gbps` 6.0): "the floor argument from drive 1
      does not apply"; it routes the case to the yellow-`link` sentence instead.
- [x] Every test that reads `SKILL.md`, plus `tests/test_translations.py`: 194 passed.

## Gaps the GREEN run reported, and their decision

- Declined, excerpt effect: how to run elevated, and why a null port reading occurs, are answered
  by "Five things need root or Administrator, not one" and the Windows port-capability paragraph
  in the same file, outside the excerpt the probe was shown.
- Declined, out of scope: whether a faster seat is irrelevant for a drive at its own maximum, and
  telling a legacy bridge from an unprivileged run. Both predate this change and are not what it
  teaches.

## Quality

- [x] No frontmatter change.
- [x] No address, hostname or path added.
