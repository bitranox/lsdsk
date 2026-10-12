"""A filesystem or inode table at or above a threshold is a finding, and only then.

Each case sits on the limit and one step either side of it, so a mutated edge
(``>`` for ``>=``) and a limit that slid by one both fail.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.config.tunables import read_thresholds
from lsdsk.adapters.render.tables import render_filesystems
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import Severity
from lsdsk.domain.filesystem_fill import diagnose_filesystems
from lsdsk.domain.models import Filesystem, FilesystemKind, Inventory
from lsdsk.domain.thresholds import Thresholds

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib_layered_config import Config


def _inventory(*rows: Filesystem) -> Inventory:
    return Inventory(hostname="t", filesystems=rows)


def _mount(name: str = "/var", fs_type: str = "ext4", **fields: object) -> Filesystem:
    return Filesystem(kind=FilesystemKind.MOUNT, name=name, fs_type=fs_type, disks=("sda",)).with_changes(**fields)


@pytest.mark.parametrize(
    ("percent", "severity"),
    [(84, None), (85, Severity.HINT), (94, Severity.HINT), (95, Severity.WARNING), (100, Severity.WARNING)],
)
def test_space_is_judged_at_or_above_each_threshold(percent: int, severity: Severity | None) -> None:
    fs = _mount(used_bytes=percent, free_bytes=100 - percent, size_bytes=100)
    found = diagnose_filesystems(_inventory(fs))
    assert [f.severity for f in found] == ([] if severity is None else [severity])


@pytest.mark.parametrize(
    ("percent", "severity"),
    [(84, None), (85, Severity.HINT), (94, Severity.HINT), (95, Severity.WARNING), (100, Severity.WARNING)],
)
def test_inodes_are_judged_at_or_above_each_threshold(percent: int, severity: Severity | None) -> None:
    fs = _mount(inodes_total=100, inodes_free=100 - percent)
    found = diagnose_filesystems(_inventory(fs))
    assert [f.severity for f in found] == ([] if severity is None else [severity])


def test_the_space_finding_says_what_it_measured_and_what_to_do() -> None:
    fs = _mount(used_bytes=96_000_000_000, free_bytes=4_000_000_000, size_bytes=100_000_000_000)
    (finding,) = diagnose_filesystems(_inventory(fs))
    assert finding.subject == "/var"
    assert finding.title == "/var is 96% full, at or above the 95% warning threshold"
    assert finding.detail == "96GB/89GiB of 100GB/93GiB used, 4.0GB/3.7GiB free, on sda."
    assert finding.action is not None
    assert finding.action.startswith("Free space or grow the filesystem before it fills")


def test_the_size_falls_back_to_used_plus_free_when_it_was_not_read() -> None:
    fs = _mount(used_bytes=90_000_000_000, free_bytes=10_000_000_000)
    (finding,) = diagnose_filesystems(_inventory(fs))
    assert finding.title == "/var is 90% full, at or above the 85% hint threshold"
    assert finding.detail == "90GB/84GiB of 100GB/93GiB used, 10GB/9.3GiB free, on sda."


def test_a_small_filesystem_with_space_free_never_reads_zero_free() -> None:
    # A nearly full /boot, the commonest real case: 486 MB with 36 MB free once
    # read "0.4 GB of 0.5 GB used, 0.0 GB free".
    boot = _mount(name="/boot", used_bytes=420_000_000, free_bytes=36_000_000, size_bytes=486_000_000)
    (finding,) = diagnose_filesystems(_inventory(boot))
    assert finding.detail == "420MB/401MiB of 486MB/463MiB used, 36MB/34MiB free, on sda."


def test_the_detail_writes_each_figure_as_the_table_beside_it_does() -> None:
    boot = _mount(name="/boot", used_bytes=420_000_000, free_bytes=36_000_000, size_bytes=486_000_000)
    inventory = _inventory(boot)
    (finding,) = diagnose_filesystems(inventory)
    console = Console(width=200, record=True, file=io.StringIO(), legacy_windows=False, no_color=True)
    console.print(render_filesystems(inventory, [finding], width=200))
    table = console.export_text()

    match = re.fullmatch(r"(\S+) of (\S+) used, (\S+) free, on sda\.", finding.detail or "")
    assert match is not None, finding.detail
    # The same three figures, spelled the same way, in the table's own cells.
    for figure in match.groups():
        assert f" {figure} " in table, (figure, table)


def test_the_inode_finding_names_inodes_not_bytes() -> None:
    (finding,) = diagnose_filesystems(_inventory(_mount(inodes_total=1000, inodes_free=30)))
    assert finding.title == "/var has used 97% of its inodes, at or above the 95% warning threshold"
    assert finding.detail == "970 of 1000 inodes used, 30 free, on sda."
    assert finding.action is not None
    assert finding.action.startswith("Remove small files or rebuild it with more inodes")


def test_a_space_finding_on_no_inventoried_disk_names_none() -> None:
    fs = _mount(used_bytes=96_000_000_000, free_bytes=4_000_000_000, size_bytes=100_000_000_000, disks=())
    (finding,) = diagnose_filesystems(_inventory(fs))
    assert finding.detail == "96GB/89GiB of 100GB/93GiB used, 4.0GB/3.7GiB free."


def test_an_inode_finding_on_no_inventoried_disk_names_none() -> None:
    (finding,) = diagnose_filesystems(_inventory(_mount(inodes_total=1000, inodes_free=30, disks=())))
    assert finding.detail == "970 of 1000 inodes used, 30 free."


def test_a_read_only_mount_is_never_judged() -> None:
    fs = _mount("/snap", "squashfs", used_bytes=100, free_bytes=0, inodes_total=10, inodes_free=0, read_only=True)
    assert diagnose_filesystems(_inventory(fs)) == []


def test_inodes_are_judged_only_on_a_fixed_table() -> None:
    ext4 = _mount("/a", inodes_total=100, inodes_free=3)
    xfs = _mount("/b", "xfs", inodes_total=100, inodes_free=3)
    titles = [f.title for f in diagnose_filesystems(_inventory(ext4, xfs))]
    assert titles == ["/a has used 97% of its inodes, at or above the 95% warning threshold"]


def test_an_unread_figure_raises_nothing() -> None:
    assert diagnose_filesystems(_inventory(_mount(used_bytes=99))) == []


def test_a_full_filesystem_with_a_full_inode_table_raises_both() -> None:
    fs = _mount(used_bytes=99, free_bytes=1, inodes_total=100, inodes_free=0)
    assert len(diagnose_filesystems(_inventory(fs))) == 2


def test_the_thresholds_are_the_configured_ones() -> None:
    fs = _mount("/v", used_bytes=50, free_bytes=50, inodes_total=100, inodes_free=50)
    tight = Thresholds(filesystem_full_hint_percent=40, filesystem_full_warning_percent=50)
    assert [f.severity for f in diagnose_filesystems(_inventory(fs), tight)] == [Severity.WARNING]
    tight_inodes = Thresholds(inodes_full_hint_percent=40, inodes_full_warning_percent=60)
    assert [f.severity for f in diagnose_filesystems(_inventory(fs), tight_inodes)] == [Severity.HINT]


def test_an_inventory_that_read_no_filesystems_raises_nothing() -> None:
    assert diagnose_filesystems(Inventory(hostname="t")) == []


def test_diagnose_includes_the_filesystem_findings() -> None:
    found = diagnose(_inventory(_mount(used_bytes=99, free_bytes=1)))
    assert [f.subject for f in found] == ["/var"]


@pytest.mark.parametrize("field", ["filesystem_full_hint_percent", "inodes_full_warning_percent"])
@pytest.mark.parametrize(("bad", "message"), [(0, "greater than or equal to 1"), (101, "less than or equal to 100")])
def test_a_percent_outside_one_to_a_hundred_is_refused_by_the_model(field: str, bad: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Thresholds().with_changes(**{field: bad})


@pytest.mark.parametrize(
    "key",
    [
        "filesystem_full_hint_percent",
        "filesystem_full_warning_percent",
        "inodes_full_hint_percent",
        "inodes_full_warning_percent",
    ],
)
def test_each_configured_key_reaches_the_field_the_rule_reads(
    config_factory: Callable[[dict[str, Any]], Config], key: str
) -> None:
    """The four keys are read by name, so a swapped pair would pass every rule test above."""
    reading = read_thresholds(config_factory({"thresholds": {key: 42}}))
    assert getattr(reading.thresholds, key) == 42
    others = {name: value for name, value in reading.thresholds.model_dump().items() if name != key}
    assert others == {name: value for name, value in Thresholds().model_dump().items() if name != key}
    assert reading.rejected == ()
