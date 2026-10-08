# Checklist - `trend --format json` carries the trend

## The change this documents

`lsdsk trend --format json` now puts the trend into the envelope as `data.trend`: one entry per
row of the printed table, each with the device, the counter and the domain's own `Trend` (verdict,
total, delta, span, rate, `were due` figure). Every other command's envelope carries
`"trend": null`. Before, the JSON held no rate, verdict or `were due` figure at all.

## Change

- The paragraph "`trend --format json` does not carry the trend" is replaced by one naming
  `data.trend`, the six counter values (with `crc_errors` mapped to the table's `interface CRC`),
  the five verdict values and the trend fields, each `null` where the table shows a dash.
- It quotes one real entry as the tool prints it, registered in
  `tests/test_quoted_output_is_reproduced.py` with the command that reproduces it
  (`trend --format json` over the two-capture history the table sample already uses).
- No frontmatter changed.

## RED

- [x] Retrieval probe (text-only agent, weak tier, the old paragraph and the table sample).
      Scenario: a monitoring script must alert when an interface CRC rate exceeds 100 per
      power-on hour. It answered that JSON cannot carry the rate and told the script to parse
      the human table, quoting "read the human table when you need the rate". Skill gaps: no
      findings JSON structure, no guidance on parsing the table's columns.

## GREEN

- [x] Same probe, new paragraph (with the JSON sample). It wrote
      `lsdsk trend --format json | jq '.data.trend[] | select(... (.trend.per_hour // 0) > 100)'`,
      quoting "Never scrape the table for a rate: drive a rate threshold from `data.trend`."
      Skill gap reported: the JSON name of the table's `interface CRC` counter was not stated,
      so it guessed `interface_crc`.
- [x] Diffed against RED both ways: RED's answer (scrape the table) is gone by design; nothing
      else RED produced was lost.

## REFACTOR

- [x] GREEN's gap closed: the paragraph names the six counter values and maps `crc_errors` to the
      table's `interface CRC`.
- [x] Quote-back: asked for the JSON value of the `interface CRC` row as a direct quote or NONE; the
      probe quoted "`crc_errors` (the table's `interface CRC`)".
- [x] Declined: the quote-back probe's nesting guess (`.per_hour` at the top level) came from an
      excerpt that omitted the JSON sample; the shipped paragraph carries the sample, and the GREEN
      probe given it wrote `.trend.per_hour`.

## Deployment

- [x] Every test that reads SKILL.md passes (164), including the quoted-output registry.
- [x] No address, host or path from a real machine added; `/dev/sdc` comes from a committed capture.
- [x] Plugin version bumped to 1.7.1 with the change, since 1.7.0 is already consumed.
