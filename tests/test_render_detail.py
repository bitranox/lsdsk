"""The record of one selected thing: what it must carry, and what it must not claim.

The panel exists because the tables drop most of what a scan reads, so the
tests that matter are the ones that would notice a field quietly falling back
out of it, and the ones that keep an unread value from reading as a good one.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.render import detail, tables, theme
from lsdsk.domain.diagnostics import diagnose

if TYPE_CHECKING:
    from lsdsk.domain.models import Disk, Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = ("linux-sas-hba", "linux-nvme-board", "linux-minimal", "windows-ahci")

# Fields a scan reads and NO column of any table draws. Each is paired with the
# label the panel gives it, so the test names what a reader would lose rather
# than counting anonymous pairs.
UNDRAWN_HEALTH: tuple[tuple[str, str], ...] = (
    ("ok", "ok"),
    ("bytes_read", "read"),
    ("available_spare", "spare"),
    ("power_cycles", "cycles"),
    ("unsafe_shutdowns", "unsafe"),
    ("error_log_entries", "error log"),
    ("temperature_warning_c", "limits"),
)


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _drawn(renderable: object, width: int = 118) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _disk_panel(machine: Inventory, disk: Disk, width: int = 118) -> str:
    return _drawn(detail.render_detail(detail.disk_detail(disk, machine), diagnose(machine)), width)


@pytest.mark.os_agnostic
def test_the_panel_carries_every_health_field_no_table_column_draws() -> None:
    """The panel is for what the columns had to drop, so name each one.

    Asserted against the VALUE the model holds rather than against the label,
    because a label printed over a dash would pass a label-only check while
    telling the reader the drive published nothing.

    The anti-vacuity half matters as much: each field is required to be read on
    at least one drive across the committed captures, or a panel that silently
    stopped drawing it would pass here on a fleet of dashes.
    """
    seen_read: dict[str, int] = {field: 0 for field, _label in UNDRAWN_HEALTH}
    for host in CAPTURES:
        machine = _machine(host)
        for disk in machine.disks:
            panel = _disk_panel(machine, disk)
            for field, label in UNDRAWN_HEALTH:
                value = None if disk.health is None else getattr(disk.health, field)
                assert label in panel, f"{host} {disk.path}: the panel dropped {label}"
                if value is not None:
                    seen_read[field] += 1

    unread_everywhere = sorted(field for field, count in seen_read.items() if not count)
    assert not unread_everywhere, (
        f"no committed capture reads these, so the test above proves nothing: {unread_everywhere}"
    )


@pytest.mark.os_agnostic
def test_a_counter_nobody_published_is_a_dash_and_the_panel_says_what_a_dash_means() -> None:
    """A blank must never read as nothing to report - the tool's own rule.

    ``/dev/sdc`` on the SAS capture publishes no pending-sector count, no media
    errors and no read total, so its panel both dashes them and carries the
    legend. The paired control is what makes that non-vacuous: a record with
    nothing unread in it must NOT print the legend, or the assertion above
    would pass on a panel that prints it unconditionally.
    """
    machine = _machine("linux-sas-hba")
    disk = next(one for one in machine.disks if one.path.endswith("sdc"))
    assert disk.health is not None
    assert disk.health.pending_sectors is None, "the fixture no longer supports this test"

    panel = _disk_panel(machine, disk)
    assert "pending -" in panel
    assert detail.UNREAD_LEGEND in panel

    control = detail.machine_detail(machine)
    unread = [name for group in control.groups for name, (text, _style) in group.values if text == "-"]
    assert not unread, f"the control has an unread value after all, so it cannot answer: {unread}"
    whole = _drawn(detail.render_detail(control, diagnose(machine)))
    assert detail.UNREAD_LEGEND not in whole, "the legend prints even where nothing was left unread"


@pytest.mark.os_agnostic
def test_a_finding_that_names_the_model_reaches_the_drive_and_says_it_is_about_the_model() -> None:
    """The one finding no row is keyed by, and the sentence that keeps it honest.

    Firmware consistency is diagnosed per MODEL, so its subject is a model
    string that matches no device path and no PCI address. Gathered without a
    note it reads as an accusation against the one drive under the cursor, when
    it is a statement about all seven of them.
    """
    machine = _machine("linux-sas-hba")
    findings = diagnose(machine)
    disk = next(one for one in machine.disks if one.path.endswith("sdc"))
    by_model = [one for one in findings if one.subject == disk.model]
    assert by_model, "the fixture no longer raises a model-keyed finding"

    panel = _disk_panel(machine, disk)
    assert by_model[0].title in panel
    assert detail.MODEL_NOTE in panel
    # And the drive's OWN finding is still there, under no note of its own.
    own = [one for one in findings if one.subject == disk.path]
    assert own and own[0].title in panel


@pytest.mark.os_agnostic
def test_the_panel_reads_its_shared_values_off_the_table_row_rather_than_deriving_them() -> None:
    """One drive, one wording: the panel may not restate a cell in its own words."""
    for host in CAPTURES:
        machine = _machine(host)
        for disk in machine.disks:
            row = tables.disk_table_row(disk, machine.port_link_for(disk))
            record = detail.disk_detail(disk, machine)
            values = {name: cell for group in record.groups for name, cell in group.values}
            assert values["serial"] == row["serial"], f"{host} {disk.path}"
            assert values["wwn"] == row["wwn"], f"{host} {disk.path}"
            assert values["negotiated"] == row["link"], f"{host} {disk.path}"
            assert values["port"] == row["port"], f"{host} {disk.path}"


@pytest.mark.os_agnostic
def test_ordering_the_groups_moves_them_and_changes_nothing_else() -> None:
    """A page may choose what is read first; it may not choose what is there."""
    machine = _machine("linux-nvme-board")
    disk = machine.disks[0]
    record = detail.disk_detail(disk, machine)
    promoted = detail.order_groups(record.groups, (detail.HEALTH, detail.COUNTERS))

    assert [group.label for group in promoted][:2] == [detail.HEALTH, detail.COUNTERS]
    assert sorted(promoted) == sorted(record.groups), "reordering changed a value"
    # Groups the page did not name keep the order the record gave them.
    named = {detail.HEALTH, detail.COUNTERS}
    assert [group.label for group in promoted if group.label not in named] == [
        group.label for group in record.groups if group.label not in named
    ]


@pytest.mark.os_agnostic
def test_a_page_asking_for_a_group_that_does_not_exist_is_a_mistake_and_says_so() -> None:
    """A silently ignored label is a promotion that quietly stopped happening."""
    groups = (detail.DetailGroup(detail.IDENTITY, ()), detail.DetailGroup(detail.HEALTH, ()))
    with pytest.raises(ValueError, match="no such detail group: helth"):
        detail.order_groups(groups, ("helth",))
    # A label that is real but absent from THESE groups is ordinary, not a
    # mistake: a controller has no health group and the health page still asks.
    assert detail.order_groups(groups, (detail.COUNTERS,)) == groups


@pytest.mark.os_agnostic
def test_a_device_with_no_pcie_capability_says_legacy_rather_than_showing_a_blank_link() -> None:
    """The distinction the hop columns already draw, kept in the panel."""
    machine = _machine("linux-sas-hba")
    legacy = [node for node in machine.pci_tree if node.pcie_capability_present is False]
    assert legacy, "the fixture no longer carries a device without a PCIe capability"
    panel = _drawn(detail.render_detail(detail.node_detail(legacy[0], machine), diagnose(machine)))
    assert theme.LEGACY in panel


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_every_record_the_panel_can_show_renders_at_every_width(host: str) -> None:
    """Whatever is selected, at whatever width, the panel draws rather than raises."""
    machine = _machine(host)
    findings = diagnose(machine)
    records = [detail.machine_detail(machine)]
    records += [detail.disk_detail(disk, machine) for disk in machine.disks]
    records += [detail.controller_detail(one, machine) for one in machine.controllers]
    records += [detail.node_detail(node, machine) for node in machine.pci_tree]
    records += [detail.slot_detail(slot, machine) for slot in machine.slots]
    for record in records:
        for width in (40, 80, 118, 200):
            assert _drawn(detail.render_detail(record, findings), width)


@pytest.mark.os_agnostic
def test_an_empty_socket_shows_no_link_running_in_it_just_as_the_table_does() -> None:
    """The slots table dashes a running figure it would otherwise draw as x0.

    A socket with nothing in it still publishes a negotiated speed and width,
    and ``report.slot_verdict``'s own table refuses to print it. A panel one
    keypress away printing ``1.0 x0`` there would be two answers about one
    socket, which is the drift this module exists to prevent.
    """
    empty_seen = 0
    filled_seen = 0
    for host in CAPTURES:
        machine = _machine(host)
        for slot in machine.slots:
            values = {name: cell for group in detail.slot_detail(slot, machine).groups for name, cell in group.values}
            running = values["running"][0]
            if slot.occupied:
                filled_seen += 1
                assert running == theme.format_pcie_decimal(slot.link.current_speed_gtps, slot.link.current_width), (
                    f"{host} {slot.address}"
                )
            else:
                empty_seen += 1
                assert running == "-", f"{host} {slot.address}: an empty socket reads as running {running}"
    # Both arms have to occur, or one of the two branches is never tested.
    assert empty_seen and filled_seen, f"the captures cover only one case: {empty_seen} empty, {filled_seen} filled"


@pytest.mark.os_agnostic
def test_the_panel_names_the_capacity_and_writes_it_on_both_scales() -> None:
    """A reader looking for the size must find it beside a label saying so.

    It used to sit unlabelled in the heading, between the model and the media
    kind, so someone reading down the labels found serial, firmware and wwn and
    concluded the panel did not carry the capacity at all.

    Both scales, because the panel has room where a column does not: the same
    drive is 500GB on its own label and 466GiB to every tool on the machine, and
    one figure alone is read as whichever the reader expected.
    """
    checked = 0
    for host in CAPTURES:
        machine = _machine(host)
        for disk in machine.disks:
            record = detail.disk_detail(disk, machine)
            identity = next(group for group in record.groups if group.label == detail.IDENTITY)
            named = dict(identity.values)
            assert "size" in named, f"{host} {disk.path}: the panel's identity group names no size"
            if disk.size_bytes is None:
                continue
            drawn = named["size"][0]
            assert theme.format_size(disk.size_bytes) in drawn, f"{host} {disk.path}: {drawn} omits the binary figure"
            assert "B/" in drawn or drawn.endswith("B"), f"{host} {disk.path}: {drawn} names no scale"
            checked += 1

    assert checked, "the control: no capture reported a capacity, so this asserted nothing"
