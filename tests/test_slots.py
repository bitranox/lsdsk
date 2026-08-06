"""Tests for the mainboard slot view.

The view exists to answer "where could this card go" at a glance, so every
verdict it prints is an assertion about the hardware and each one is pinned
here, including the cases that must stay silent about what was not measured.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.linux.reader import parse_pcie_capability
from lsdsk.adapters.render.report import (
    SLOTS_NEEDING_ROOT,
    form_factor_note,
    render_slots,
    slot_privilege_note,
    slot_verdict,
)
from lsdsk.domain.models import Inventory, PcieLink, PcieSlot

if TYPE_CHECKING:
    from collections.abc import Callable

# A PCI Express capability at offset 0x40 of an otherwise empty config space.
_CAP_OFFSET = 0x40


def config_with_capability(*, slot_implemented: bool, slot_number: int) -> bytes:
    """Build a config space carrying one PCI Express capability.

    Mirrors the real layout: the capability pointer at 0x34, the capability
    identifier and the PCI Express Capabilities register at its head, and the
    Slot Capabilities register 0x14 further in.
    """
    config = bytearray(256)
    config[0x34] = _CAP_OFFSET
    config[_CAP_OFFSET] = 0x10  # PCI Express capability id
    capabilities = (1 << 8) if slot_implemented else 0
    config[_CAP_OFFSET + 2 : _CAP_OFFSET + 4] = capabilities.to_bytes(2, "little")
    slot_capabilities = slot_number << 19
    config[_CAP_OFFSET + 0x14 : _CAP_OFFSET + 0x18] = slot_capabilities.to_bytes(4, "little")
    return bytes(config)


@pytest.mark.os_agnostic
def test_the_physical_slot_number_is_decoded_from_slot_capabilities() -> None:
    """Verify the number a board manual labels its slots by is read correctly."""
    decoded = parse_pcie_capability(config_with_capability(slot_implemented=True, slot_number=16))

    assert decoded.slot_implemented is True
    assert decoded.physical_slot_number == 16


@pytest.mark.os_agnostic
def test_a_port_without_a_connector_reports_no_slot_number() -> None:
    """Verify the register is not read where it is undefined.

    Slot Capabilities means nothing unless a slot is implemented, so a port with
    no connector would otherwise report whatever zero the register happens to
    hold as though it were slot 0.
    """
    decoded = parse_pcie_capability(config_with_capability(slot_implemented=False, slot_number=16))

    assert decoded.slot_implemented is False
    assert decoded.physical_slot_number is None


@pytest.mark.os_agnostic
def test_a_truncated_config_space_yields_nothing_rather_than_a_guess() -> None:
    """Verify an unprivileged read reports unknown, not a fabricated slot."""
    decoded = parse_pcie_capability(bytes(64))

    assert decoded.slot_implemented is None
    assert decoded.physical_slot_number is None


def slot(**kwargs: object) -> PcieSlot:
    """Build a port for one verdict under test."""
    defaults: dict[str, object] = {"address": "0000:00:01.0", "link": PcieLink(8.0, 8, 8.0, 8)}
    defaults.update(kwargs)
    return PcieSlot(**defaults)  # pyright: ignore[reportArgumentType] - test helper forwarding optional fields


@pytest.mark.os_agnostic
def test_an_empty_port_with_a_proven_connector_reads_free() -> None:
    """Verify a real free socket is called out, since that is the useful case."""
    assert slot_verdict(slot(connector_present=True))[0] == "FREE"


@pytest.mark.os_agnostic
def test_an_empty_port_without_a_proven_connector_withholds_free() -> None:
    """Verify absence of a connector reading is not turned into a free socket.

    An internal port to a soldered-down device is empty in exactly the same way
    as a real slot, so claiming one sends somebody looking for a connector that
    is not there.
    """
    assert slot_verdict(slot())[0] == "empty, connector unknown"
    assert slot_verdict(slot(connector_present=False))[0] == "no connector"


@pytest.mark.os_agnostic
def test_unused_bandwidth_is_reported_even_when_no_move_could_be_proposed() -> None:
    """Verify headroom is a measurement, not gated behind the connector reading.

    Measured on a Z690: a 10G NIC in a Gen5 x8 port leaves about 27 GB/s unused.
    Gating that number behind is_swap_candidate hid it entirely on any
    unprivileged run, which is exactly when somebody is looking for it.
    """
    nic = slot(
        link=PcieLink(32.0, 8, 32.0, 8),
        occupied=True,
        occupant_class=0x020000,
        occupant_link=PcieLink(5.0, 8, 5.0, 8),
    )

    verdict = slot_verdict(nic)[0]

    assert verdict.startswith("spare ")
    assert nic.connector_present is None, "the point is that this is unknown"
    assert not nic.is_swap_candidate, "and that no move can be proposed"


@pytest.mark.os_agnostic
def test_a_graphics_card_is_never_offered_as_spare_capacity() -> None:
    """Verify the display exclusion holds in the view as well as the rules."""
    gpu = slot(
        link=PcieLink(32.0, 16, 32.0, 16),
        occupied=True,
        connector_present=True,
        occupant_class=0x030000,
        occupant_link=PcieLink(8.0, 4, 8.0, 4),
    )

    assert slot_verdict(gpu)[0] == "in use (graphics)"


@pytest.mark.os_agnostic
def test_a_port_slower_than_its_occupant_says_so() -> None:
    """Verify the port-limited case is named rather than called full."""
    limited = slot(
        link=PcieLink(8.0, 4, 8.0, 4),
        occupied=True,
        occupant_class=0x010802,
        occupant_link=PcieLink(8.0, 4, 16.0, 4),
    )

    assert slot_verdict(limited)[0] == "port limits it"


@pytest.mark.os_agnostic
def test_the_view_always_says_the_form_factor_is_unknown(rendered: Callable[..., str]) -> None:
    """Verify nobody is left to infer M.2 from the width.

    No readable source gives the form factor, and an x4 port is as likely a card
    slot as an M.2 socket, so silence here would invite a wrong guess.
    """
    board = Inventory("h", board="Test Board", slots=(slot(connector_present=True),), privileged=True)

    output = rendered(render_slots(board))

    assert "Form factor is not reported" in output
    assert "board manual" in output


@pytest.mark.os_agnostic
def test_the_privilege_note_appears_only_when_privilege_is_the_cause() -> None:
    """Verify the note names the real cause and does not fire when data was read."""
    unprivileged = Inventory("h", privileged=False)
    privileged = Inventory("h", privileged=True)

    note = slot_privilege_note(unprivileged)

    assert "Run as root" in note
    for name in SLOTS_NEEDING_ROOT:
        assert name in note, f"{name} is promised by the model but not named to the reader"
    assert slot_privilege_note(privileged) == ""


@pytest.mark.os_agnostic
def test_in_a_container_the_note_does_not_send_anybody_to_sudo() -> None:
    """Verify elevating is not advised where it cannot help."""
    from lsdsk.domain.enums import Environment

    contained = Inventory("h", privileged=False, environment=Environment.CONTAINER)

    note = slot_privilege_note(contained)

    assert "Run as root" not in note
    assert "on the host" in note


@pytest.mark.os_agnostic
def test_the_form_factor_note_never_claims_a_form_factor() -> None:
    """Verify the note cannot drift into asserting what it says is unknowable."""
    note = form_factor_note()

    assert "M.2" in note
    assert "not reported" in note


@pytest.mark.os_agnostic
def test_the_full_page_carries_every_section(rendered: Callable[..., str]) -> None:
    """Verify one page really does answer every question the others do.

    The point of the view is that nobody has to know which command to run, so a
    section quietly missing defeats it entirely.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.adapters.render.full import render_full
    from lsdsk.domain.diagnostics import diagnose

    fixture = Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json"
    inventory = load(fixture)

    output = rendered(render_full(inventory, diagnose(inventory)), width=200)

    assert "MEG Z690 ACE" in output, "the mainboard"
    assert "PROBLEMS" in output, "the problem summary"
    assert "Topology on linux-nvme-board" in output, "the controller tree"
    assert "Controllers on linux-nvme-board" in output, "the controller table"
    assert "Disks on linux-nvme-board" in output, "the disk identities"
    assert "Disk health on linux-nvme-board" in output, "wear and error counters"
    assert "18 ports" in output, "the mainboard slots"
    assert "counter history" in output, "what the error counters are doing over time"
    assert "Findings on linux-nvme-board" in output, "the findings with their reasoning"


@pytest.mark.os_agnostic
def test_the_full_page_leads_with_what_is_wrong(rendered: Callable[..., str]) -> None:
    """Verify the ordering, because a long page is read from the top.

    Somebody who stops after the first screen must still have seen everything
    actionable, so the summary precedes the detail it summarises.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.adapters.render.full import render_full
    from lsdsk.domain.diagnostics import diagnose

    fixture = Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json"
    inventory = load(fixture)

    output = rendered(render_full(inventory, diagnose(inventory)), width=200)

    assert output.index("PROBLEMS") < output.index("Topology on"), "problems come before topology"
    assert output.index("Topology on") < output.index("Disk health on"), "topology before health"
    assert output.index("Disk health on") < output.index("Findings on"), "detail before full reasoning"


@pytest.mark.os_agnostic
def test_the_smart_page_shows_every_disk_without_being_asked(rendered: Callable[..., str]) -> None:
    """Verify all attributes are on the page, so nothing has to be selected.

    The question the page answers is "is anything about to fail", which cannot
    be answered one drive at a time.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.adapters.render.report import render_smart

    inventory = load(Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json")

    output = rendered(render_smart(inventory), width=140)

    assert "select a disk" not in output.lower(), "the page must not ask for an impossible interaction"
    for disk in inventory.disks:
        assert disk.path in output, f"{disk.path} is absent, so the page is not complete"
    reporting = [d for d in inventory.disks if d.health and d.health.attributes]
    assert reporting, "the fixture must have at least one drive with an attribute table"
    # One heading per reporting drive, so a drive cannot be counted twice or
    # quietly share another's table. Matched on the numeric form, because the
    # page title carries the bare word too.
    assert len(re.findall(r"\d+ attributes", output)) == len(reporting)
    assert "Reallocated_Sector_Ct" in output


@pytest.mark.os_agnostic
def test_a_drive_with_no_attribute_table_says_why(rendered: Callable[..., str]) -> None:
    """Verify an NVMe drive is not silently blank.

    NVMe has no ATA attribute table at all; it publishes a fixed health log.
    That is a different thing from an ATA drive whose table could not be read,
    and a reader who cannot tell them apart goes looking for a fault.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.adapters.render.report import render_smart
    from lsdsk.domain.enums import BusType

    inventory = load(Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json")
    nvme = next(disk for disk in inventory.disks if disk.bus is BusType.NVME)

    output = rendered(render_smart(inventory), width=140)

    assert nvme.path in output
    assert "fixed health log" in output


@pytest.mark.os_agnostic
def test_an_nvme_row_is_graded_like_any_other_disk() -> None:
    """Verify a PCIe disk is compared against its seat, not called healthy.

    An NVMe drive is its own controller, so its seat is that controller's
    upstream link. Copying the drive's own maximum into the port column made the
    port look like it was never the constraint, and colouring the link green
    whenever it trained at all made a Gen4 drive in a Gen3 seat read as fine.
    Measured on a Z690: a 980 PRO in a Gen3 x4 socket.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.adapters.render import report, theme

    inventory = load(Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json")
    drive = next(disk for disk in inventory.disks if disk.node == "nvme4n1")
    port = inventory.port_link_for(drive)

    cells = report.disk_cells(drive, port)
    styles = report.disk_cell_styles(drive, port)

    assert cells["port"] == "Gen3 x4", "the port column must show the seat, not the drive"
    assert cells["disk"] == "Gen4 x4"
    assert cells["link"] == "Gen3 x4"
    assert styles["port"] == theme.STYLE_BELOW_CAPABILITY, "the seat is the constraint, so it carries the colour"
    assert styles["link"] == theme.STYLE_AT_CAPABILITY, "the link reached what the pairing allows, so it is not a fault"


@pytest.mark.os_agnostic
def test_every_view_grades_a_disk_the_same_way() -> None:
    """Verify the tree and the table cannot disagree about one disk.

    Three copies of this logic existed and one of them called every NVMe link
    healthy whatever it negotiated.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.adapters.render import report, tables
    from lsdsk.domain.diagnostics import diagnose

    inventory = load(Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json")
    findings = diagnose(inventory)
    drive = next(disk for disk in inventory.disks if disk.node == "nvme4n1")
    expected = report.disk_cells(drive, inventory.port_link_for(drive))

    table = tables.render_disks(inventory, findings, width=200)
    rendered_columns = [str(column.header) for column in table.columns]

    assert "port" in rendered_columns
    row = next(
        cells
        for cells in zip(*[list(column.cells) for column in table.columns], strict=True)
        if any(str(cell) == drive.path for cell in cells)
    )
    texts = [str(cell) for cell in row]
    assert expected["port"] in texts, f"the table shows a different port than the tree: {texts}"
