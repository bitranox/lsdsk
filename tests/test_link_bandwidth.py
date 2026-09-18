"""What a link figure is worth, and what a narrow terminal gives up to say so.

A link shape says nothing about throughput to a reader who does not carry the
PCIe lane table in their head, so every figure drawn in a column carries its own
bandwidth. Two things can go wrong with that and both are silent, so both are
held here: the number can sit beside the WRONG figure, and it can cost a column
somebody actually asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.render import detail, report, tables, theme
from lsdsk.adapters.render.layout import Column, Layout
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.models import PcieLink

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = ("linux-sas-hba", "linux-nvme-board", "linux-minimal", "windows-ahci")

#: Widths to sweep. Spans the tool's own piped default (120) and the width its
#: pictures are taken at (180), plus the narrow end where columns start going.
WIDTHS = tuple(range(40, 201, 4))


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _texts(rows: list[dict[str, tuple[str, str]]]) -> list[dict[str, str]]:
    return [{key: value[0] for key, value in row.items()} for row in rows]


def _group(record: detail.Detail, label: str) -> dict[str, str]:
    """One NAMED group's values, never the record flattened.

    A controller's record carries ``running`` and ``capable`` in its link group
    AND again in its upstream group, so flattening every group into one dict
    silently keeps the upstream's - which is a different link, on a different
    device, and it reads exactly like the one being asked about.
    """
    for group in record.groups:
        if group.label == label:
            return {name: cell[0] for name, cell in group.values}
    raise AssertionError(f"no {label} group in this record")


def _surfaces(machine: Inventory) -> list[tuple[str, tuple[Column, ...], list[dict[str, str]], list[dict[str, str]]]]:
    """Every table the surrender rule is claimed to hold for, with its rows.

    One implementation serves four column sets - the two disk tables, the
    controllers table and the slots table - and only the new disk table was ever
    swept. A rule asserted on one of its four surfaces is a rule three of them
    could break without anything going red.

    Args:
        machine: The inventory whose real rows are laid out.

    Returns:
        One entry per surface: a label, its columns, and its rows with and
        without the bandwidth.
    """
    findings = diagnose(machine)
    return [
        (
            "the disk table",
            tables.DISK_COLUMNS,
            _texts([tables.disk_table_row(d, machine.port_link_for(d), bandwidth=True) for d in machine.disks]),
            _texts([tables.disk_table_row(d, machine.port_link_for(d)) for d in machine.disks]),
        ),
        (
            "the disk-and-controller tree's disk table",
            report.DISK_COLUMNS,
            _texts([report.disk_row(d, machine.port_link_for(d), bandwidth=True) for d in machine.disks]),
            _texts([report.disk_row(d, machine.port_link_for(d)) for d in machine.disks]),
        ),
        (
            "the controllers table",
            tables.CONTROLLER_COLUMNS,
            _texts(
                [tables.controller_table_row(c, machine, findings, bandwidth=True).cells for c in machine.controllers]
            ),
            _texts([tables.controller_table_row(c, machine, findings).cells for c in machine.controllers]),
        ),
        (
            "the slots table",
            report.SLOT_COLUMNS,
            _texts([report.slot_table_row(slot, bandwidth=True) for slot in machine.slots]),
            _texts([report.slot_table_row(slot) for slot in machine.slots]),
        ),
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_the_bandwidth_is_surrendered_before_any_column_is(host: str) -> None:
    """A column is a fact somebody asked for; the bandwidth is a gloss on a figure.

    So at any width where carrying it would cost a column, it goes instead. The
    user set this constraint in so many words about ``size``, which is priority
    3 and 4 in the two disk tables and was measured disappearing from both below
    about 120 columns when the figures simply grew - at 120, the width every
    piped and redirected run uses.

    Asserted as the RULE over every capture rather than as a table of widths,
    because the figures differ per machine and a literal would only agree with
    whatever the code did.
    """
    machine = _machine(host)
    surfaces = _surfaces(machine)
    swept = 0
    for label, columns, rich, plain in surfaces:
        if not rich:
            continue
        swept += 1
        for width in WIDTHS:
            chosen = Layout.preferring(columns, rich, plain, width)
            bare = Layout.for_rows(columns, plain, width)
            kept = [column.key for column in chosen.columns]
            assert kept == [column.key for column in bare.columns], (
                f"{host}, {label} at {width}: carrying the bandwidth cost "
                f"{[k for k in (c.key for c in bare.columns) if k not in kept]}"
            )
            assert chosen.required() <= max(width, bare.required()), (
                f"{host}, {label} at {width}: the row needs {chosen.required()} of {width}"
            )
    # The control. A surface whose rows came back empty is swept vacuously, and
    # the whole point of this test is that three of the four were never swept at
    # all; a silent skip would restore exactly that.
    assert swept == len(surfaces), f"{host}: only {swept} of {len(surfaces)} surfaces had rows to lay out"

    # A machine whose rows are short enough to carry the bandwidth at every width
    # is a legitimate answer here, so the both-arms check belongs to the captures
    # TOGETHER and is made in the test below rather than vacuously here.


@pytest.mark.os_agnostic
def test_the_sweep_sees_both_a_width_that_affords_the_bandwidth_and_one_that_does_not() -> None:
    """A rule that never fires is not a rule, and one that always fires is not either.

    Held over the captures TOGETHER, because a machine with short rows can
    honestly carry the bandwidth at every width and a machine with long ones can
    honestly refuse it at most.
    """
    affords = refuses = 0
    for host in CAPTURES:
        machine = _machine(host)
        if not machine.disks:
            continue
        rich = [tables.disk_table_row(disk, machine.port_link_for(disk), bandwidth=True) for disk in machine.disks]
        plain = [tables.disk_table_row(disk, machine.port_link_for(disk)) for disk in machine.disks]
        for width in WIDTHS:
            if Layout.preferring(tables.DISK_COLUMNS, _texts(rich), _texts(plain), width).bandwidth:
                affords += 1
            else:
                refuses += 1
    assert affords and refuses, f"the sweep saw only one answer: {affords} affording, {refuses} refusing"


@pytest.mark.os_agnostic
def test_surrendering_it_is_what_keeps_size_on_the_page() -> None:
    """The rule above, stated as the consequence the user asked for.

    Written against a width MEASURED to be one the bandwidth does not fit, so
    the test cannot go quietly vacuous the day the figures shrink: if no such
    width exists in the sweep it fails rather than passing.
    """
    machine = _machine("linux-nvme-board")
    rich = [tables.disk_table_row(disk, machine.port_link_for(disk), bandwidth=True) for disk in machine.disks]
    plain = [tables.disk_table_row(disk, machine.port_link_for(disk)) for disk in machine.disks]

    tight = [
        width
        for width in WIDTHS
        if not Layout.preferring(tables.DISK_COLUMNS, _texts(rich), _texts(plain), width).bandwidth
        and "size" in [column.key for column in Layout.for_rows(tables.DISK_COLUMNS, _texts(plain), width).columns]
    ]
    assert tight, "no width in the sweep both refuses the bandwidth and has room for size"

    for width in tight:
        chosen = Layout.preferring(tables.DISK_COLUMNS, _texts(rich), _texts(plain), width)
        assert "size" in [column.key for column in chosen.columns], f"size was dropped at {width}"


@pytest.mark.os_agnostic
def test_a_rich_row_that_cannot_be_narrowed_is_refused_rather_than_run_off_the_side() -> None:
    """``fit`` cannot drop a priority-0 column, so it can hand back a set that does not fit.

    That is the case the column-set comparison alone would miss: nothing was
    dropped, so the two sets agree, while the row overruns the terminal. ``link``
    is priority 0 in both disk tables, so this is not hypothetical.
    """
    columns = (Column("a", "a", priority=0), Column("b", "b", priority=0))
    rich = [{"a": "x", "b": "a very long value indeed"}]
    plain = [{"a": "x", "b": "short"}]

    chosen = Layout.preferring(columns, rich, plain, 20)
    assert not chosen.bandwidth, "the fuller row does not fit 20 columns and must have been refused"
    assert [column.key for column in chosen.columns] == ["a", "b"], "neither column is droppable"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_a_bandwidth_sits_beside_the_figure_it_belongs_to(host: str) -> None:
    """The panel used to end a link line with the CAPABLE throughput.

    The first value on that line was the RUNNING link, and a reader comparing a
    downgraded card against its own capability takes the number nearest what
    they were looking at. So this asserts whose each number is, not that a
    number is present: ``running`` must carry what the NEGOTIATED link carries
    and ``capable`` what the MAXIMUM does.

    It is written to fail against the old shape: there, ``running`` carried no
    figure at all and the capable one stood alone under a third label.
    """
    machine = _machine(host)
    seen = 0
    for controller in machine.controllers:
        link = controller.link
        if link.current_bandwidth_gbps is None or link.max_bandwidth_gbps is None:
            continue
        values = _group(detail.controller_detail(controller, machine), detail.LINK)
        seen += 1
        assert theme.format_bandwidth(link.current_bandwidth_gbps) in values["running"], (
            f"{host} {controller.address}: running reads {values['running']!r}"
        )
        assert theme.format_bandwidth(link.max_bandwidth_gbps) in values["capable"], (
            f"{host} {controller.address}: capable reads {values['capable']!r}"
        )
        assert "carries" not in values, "the stray third figure is what put a capable number beside a running one"

    if not seen:
        pytest.skip(f"{host} publishes no controller link with both ends read")


@pytest.mark.os_agnostic
def test_a_downgraded_link_is_the_case_the_two_numbers_have_to_differ_on() -> None:
    """A pair where both numbers are equal cannot tell a swap from a correct pairing.

    So the assertion above is re-run here on a link that is provably running
    below its own capability, where crossing the two numbers produces a visibly
    wrong panel rather than an identical one.
    """
    machine = _machine("linux-nvme-board")
    downgraded = [
        controller
        for controller in machine.controllers
        if controller.link.is_downgraded
        and controller.link.current_bandwidth_gbps != controller.link.max_bandwidth_gbps
    ]
    assert downgraded, "this capture is supposed to hold a card capped by its port"

    for controller in downgraded:
        values = _group(detail.controller_detail(controller, machine), detail.LINK)
        running, capable = values["running"], values["capable"]
        assert theme.format_bandwidth(controller.link.current_bandwidth_gbps) in running
        assert theme.format_bandwidth(controller.link.max_bandwidth_gbps) in capable
        assert running != capable, "a downgraded link must not read the same on both sides"


@pytest.mark.os_agnostic
def test_a_placeholder_carries_no_bandwidth() -> None:
    """A dash cannot carry a throughput, and a figure nobody read must not gain one.

    Putting a number after an unread value is the blank-implies-fine the link
    rules refuse: it turns "we could not measure this" into a measurement.
    """
    assert theme.with_bandwidth(theme.NOT_READ, 3.94) == theme.NOT_READ
    assert theme.with_bandwidth(theme.LEGACY, 3.94) == theme.LEGACY
    assert theme.with_bandwidth("6G", None) == "6G"
    # And the control: a figure that WAS read, with a bandwidth, gains it - or
    # the three assertions above would pass on a function that never decorates
    # anything.
    assert theme.with_bandwidth("6G", 0.6) == "6G (0.60 GB/s)"


@pytest.mark.os_agnostic
def test_a_hop_column_never_decorates_a_symbol() -> None:
    """The same rule where the fabric draws it, since that column has two tiers.

    A legacy device has no link to price and an unread register is not a zero,
    so neither may come back from the wide tier wearing a figure.
    """
    from lsdsk.adapters.render.tree import hop_cells
    from lsdsk.domain.models import PciNode

    legacy = PciNode(address="a", name="b", pcie_capability_present=False)
    unread = PciNode(address="a", name="b")
    for node in (legacy, unread):
        for text, _style in hop_cells(node, bandwidth=True):
            assert "GB/s" not in text, f"{text!r} prices a link nobody read"

    read = PciNode(
        address="a",
        name="b",
        link=PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=8.0, max_width=4),
        pcie_capability_present=True,
    )
    assert "GB/s" in hop_cells(read, bandwidth=True)[0][0], "a read link must gain its figure"
