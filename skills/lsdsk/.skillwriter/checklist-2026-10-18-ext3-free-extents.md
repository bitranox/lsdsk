# Checklist - an ext3 mount carries the free-extent figures

## The change this documents

`FREE_EXTENT_MOUNT_TYPES` (`src/lsdsk/domain/models.py`) gains `ext3`: an "ext3" mount is the ext4
driver's own registration since Linux 4.3, whose ioctl answers `FS_IOC_GETFSMAP`. So an ext3 row of
`filesystems` carries all five `free_space` figures, as ext4 and xfs do.

## Change

- The `free_space` paragraph: "ext4 and xfs mounts and Windows volumes carry all five" names ext3
  too. One sentence, no frontmatter, no other line.

## RED

- [x] Route: a text check of the skill file against the code, not a behavioural probe. The claim is
      a set the code defines, so the test compares the sentence with `FREE_EXTENT_MOUNT_TYPES`
      itself rather than with a list a writer keeps.
- [x] `test_the_skill_names_exactly_the_mount_types_that_carry_free_extent_figures`
      (`tests/test_the_skill_agrees_with_the_package.py`) fails on the unchanged skill:
      "the skill names ['ext4', 'xfs']".

## GREEN

- [x] The same test passes on the edited skill, and so do the other skill tests
      (`test_the_skill_agrees_with_the_package.py`, `test_skill_describes_elevation.py`,
      `test_skill_describes_the_fabric_view.py`, `test_skill_quotes_output_the_tool_produces.py`:
      31 passed).
- [x] No behavioural probe: the change adds no instruction, only a member to a stated set, and the
      text check is the stronger evidence for a set the code owns.

## Quality

- [x] The edited line is 99 columns, within the file's wrap width.
- [x] No address, hostname or path added.
