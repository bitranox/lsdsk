# skill-writer review: where a controller's name comes from, and which name follows the OS language

A controller is now named from its numeric PCI identifiers rather than from the
operating system's device description. That fixes a German name appearing in
English output, and it creates a new question a reader will ask: lsdsk and the
Windows Device Manager now call the same device different things.

## RED

Scenario: a colleague on German Windows sees a controller named
`Samsung Electronics Co Ltd NVMe SSD Controller PM9A1/PM9A3/980PRO` where Device
Manager says `Standardmaessiger NVM Express-Controller`, and a port quoted as
`PCI-zu-PCI-Bruecke`. Are these the same device, and is the machine
misconfigured?

An isolated agent given the pre-change text reached the right answer and said so
itself that it had no basis for it:

> "I have to flag that the second half of this is not something the skill text
> actually says."

and in its gaps list:

> "The skill text has zero discussion of localization anywhere. My explanation of
> why one field follows Windows' display language and the other doesn't rests on
> one data point plus its absence for the controller field, not a stated rule."

A right answer reached by inference is not the skill working. The next reader
gets whatever that inference happens to produce.

## GREEN

Same scenario, new text. The agent quoted the governing passage for both halves,
answered both questions, and reported:

> "No contradictions found in the text on this topic ... both examples line up
> exactly with what the colleague reported, so nothing had to be guessed for the
> core answer."

## RED and GREEN diffed both ways

RED offered a check the reader can run: compare the PCI address in Device
Manager's Details tab. GREEN kept it. Nothing the baseline produced is missing.

## Gaps reported by the test runs

| Gap                                                                              | Outcome                                                                     |
|----------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| Could not tell which view surfaces `upstream_name` - a column, or only a finding | CLOSED. It is stated: not a column, only in that finding's text and in JSON |
| Could not tell whether the quoted bridge name implies a finding                  | CLOSED by the same sentence                                                 |
| Whether Device Manager's generic label means an in-box class driver              | CLOSED. The skill now says which source Device Manager shows                |

## Quote-back

Asked whether the text says if `upstream_name` is a column in the `controllers`
table, an isolated agent quoted:

> "`upstream_name` is not a column."

## Verification

- [x] RED recorded verbatim, including its own statement that it was inferring
- [x] GREEN answers from quoted text, on the same scenario
- [x] Every reported gap closed
- [x] Fix verified by quote-back
- [x] Both claims checked against the running program rather than assumed:
      `lsdsk controllers` prints no port name, no finding in that capture carries
      one, and `--format json` does expose `upstream_name`
- [x] No session narrative, no scratch paths, no machine-specific values
