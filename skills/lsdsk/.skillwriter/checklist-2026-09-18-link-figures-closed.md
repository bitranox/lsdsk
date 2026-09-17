# skill-writer review: the skill quoted link figures the tool no longer prints

`lsdsk` writes a link figure closed - `Gen4x4`, `1.0x1` - and every figure drawn
in a column now carries its throughput. The skill quoted four figures in the old
spaced spelling. Three of them are the value a rule tells the reader to look for
in their own output, so a reader searching for `Gen1 x1` finds nothing and
concludes the rule does not describe their machine.

## Route

Not a pressure scenario. The defect is that the skill quotes strings the tool
cannot produce, and the evidence for that is mechanical: feed every figure the
skill quotes back through the formatter that would draw it and require the
string to come out unchanged.

That is a better test than a scenario here for two reasons. A capable agent
reads `Gen1x1 (0.25 GB/s)` as satisfying `Gen1 x1` and would comply anyway, so a
behavioural arm would not flip on the real defect; and the mechanical check keeps
working, so it catches the next drift rather than this one only. It lives in the
repo's own suite as `tests/test_skill_quotes_output_the_tool_produces.py`, under
`make test`.

## RED

Against the unedited skill:

- `test_no_link_figure_in_the_skill_is_written_with_a_blank_inside_it` - FAILED,
  naming four figures with their line numbers.
- `test_every_link_figure_the_skill_quotes_is_one_a_formatter_produces` - FAILED
  at the first: "line 564: the skill quotes 'Gen4 x4'; the tool prints 'Gen4x4'".
- `test_the_skill_quotes_some_link_figures_at_all` - PASSED. The control, and it
  is what stops the two above passing on an empty list.
- `test_a_bandwidth_the_skill_quotes_beside_a_figure_is_that_figure_s_own` -
  SKIPPED, because the skill quoted no throughput at all.

## GREEN

The same four, against the edited skill: all four PASS, and the fourth no longer
skips - it found `1.0x1 (0.25 GB/s)` and confirmed 0.25 GB/s is what
`pcie_bandwidth_gbps` computes for one lane at 2.5 GT/s.

## What changed, and what deliberately did not

- The four figures are closed. Nothing else about the rules moved.
- The form each site uses was checked rather than assumed: the floor rule at the
  `running`/`capable` columns is about a CONTROLLER's link, which is drawn in the
  decimal spelling, and the two beside it are read off `lsdsk slots`, which draws
  the marketing one. Both were already right for their view.
- The quotes are the BARE figure, not the figure with its throughput. A narrow
  terminal surrenders the throughput before it drops a column, so the bare figure
  is the part that is present either way; one added clause says so, and names the
  fuller form the reader will usually see.

## Gaps reported, and their disposition

- The slots TABLE spells a port `Gen3x4` where the detail PANEL one keypress away
  spells it `3.0x4`. This predates the change and is a decision about which view
  should move, so it is DECLINED here and carried in `OPEN-WORK.md`.

## Checklist

- [x] Receipt issued before the edit.
- [x] RED run and recorded verbatim, with a control that fails on an empty list.
- [x] GREEN run: every arm passes, and the arm that had nothing to check now has
      something.
- [x] The test asserts the RULE (a quoted figure must be reachable from a
      formatter), not a list of corrected lines.
- [x] Frontmatter untouched; `name` and `description` unchanged, so no routing
      keyword moved.
- [x] No address, hostname or path added.
- [x] Body edit is four figures and one clause; no rule reworded.
