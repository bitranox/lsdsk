"""A function integrated into switch silicon publishes the PCIe floor, not an uplink.

A desktop chipset used as a PCIe switch is how a multi-drive expansion card is
usually built. It passes real links through to the sockets it wires, and on the
SATA and USB functions built into the chip it publishes the lowest link the
specification allows: 2.5 GT/s x1, as both running and capable. Read as a
ceiling, that register pair told the owner of a working card to replace it,
where a published review of the same card measured about 1.7 GB/s across four
SATA drives against the 0.25 GB/s the rule claimed.

The floor value alone decides nothing, because a dead or downtrained link reads
the same. What separates the two is a second function on the same switch
publishing the identical floor while another device there reports a real link,
and the switch itself has to be proved: ports on a root bus share a bus number
but are independent slots. Every test after the first removes one leg of that
and requires the warning to stand exactly as it did before the pattern was
recognised.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from lsdsk.domain.diagnostics import diagnose, diagnose_controller_oversubscription
from lsdsk.domain.enums import BusType, ControllerKind, DiskKind, Severity
from lsdsk.domain.models import Controller, Disk, InterfaceLink, Inventory, PcieLink, PcieSlot

# PCI class triples for what sits in each port. Named, because a reader of a
# test about USB and NVMe neighbours should not have to decode hex.
_AHCI = 0x010601
_USB = 0x0C0330
_NVME = 0x010802
_DISPLAY = 0x030000
_BRIDGE = 0x060400

_FLOOR = PcieLink(2.5, 1, 2.5, 1)
_SATA_ADDRESS = "0000:11:00.0"
_REPLACE_THE_CARD = "replace this card"


def _port(address: str, *, occupant: str, link: PcieLink, occupant_class: int) -> PcieSlot:
    """A switch port with a device behind it whose own link was read.

    The port's own capability is left unread, which is what Windows reports for
    every bridge, so nothing here can lean on a measured port.
    """
    return PcieSlot(
        address,
        PcieLink(),
        occupied=True,
        occupant_address=occupant,
        occupant_class=occupant_class,
        occupant_link=link,
    )


_USB_AT_THE_FLOOR = _port("0000:08:0c.0", occupant="0000:10:00.0", link=_FLOOR, occupant_class=_USB)
_NVME_WITH_A_REAL_LINK = _port(
    "0000:08:00.0", occupant="0000:09:00.0", link=PcieLink(16.0, 2, 16.0, 4), occupant_class=_NVME
)
_NVME_AT_THE_FLOOR = _port("0000:08:00.0", occupant="0000:09:00.0", link=_FLOOR, occupant_class=_NVME)
_EMPTY_PORT = PcieSlot("0000:08:04.0", PcieLink())
_USB_AT_THE_FLOOR_ON_ANOTHER_SWITCH = _port("0000:02:0c.0", occupant="0000:05:00.0", link=_FLOOR, occupant_class=_USB)
_NVME_WITH_A_REAL_LINK_ON_ANOTHER_SWITCH = _port(
    "0000:02:00.0", occupant="0000:03:00.0", link=PcieLink(16.0, 4, 16.0, 4), occupant_class=_NVME
)

# The bridge above bus 0000:08. Its record names the first downstream port as
# its occupant, and that is the whole proof that 0000:08 is a switch's internal
# bus rather than a root bus.
_SWITCH_UPSTREAM_PORT = _port("0000:07:00.0", occupant="0000:08:00.0", link=PcieLink(), occupant_class=_BRIDGE)

# A root port leading to a switch, the switch's upstream port, and an NVMe drive
# behind one of its downstream ports: bridges recorded elsewhere in the machine,
# none of them above the root bus under test.
_A_SWITCH_ELSEWHERE = (
    _port("0000:00:03.1", occupant="0000:07:00.0", link=PcieLink(), occupant_class=_BRIDGE),
    _port("0000:07:00.0", occupant="0000:08:00.0", link=PcieLink(), occupant_class=_BRIDGE),
    _port("0000:08:00.0", occupant="0000:09:00.0", link=PcieLink(16.0, 4, 16.0, 4), occupant_class=_NVME),
)


def _sata_controller(link: PcieLink = _FLOOR) -> Controller:
    """The SATA function under test, with the bridge above it unread."""
    return Controller(
        address=_SATA_ADDRESS,
        name="Chipset SATA Controller",
        kind=ControllerKind.AHCI,
        link=link,
        upstream=PcieLink(),
    )


def _drives(negotiated_gbps: float = 6.0) -> tuple[Disk, ...]:
    """Three SATA drives on the controller under test."""
    return tuple(
        Disk(
            node,
            f"/dev/{node}",
            "SATA SSD",
            kind=DiskKind.SSD,
            bus=BusType.SATA,
            controller_address=_SATA_ADDRESS,
            link=InterfaceLink(negotiated_gbps, negotiated_gbps, negotiated_gbps),
        )
        for node in ("sda", "sdb", "sdc")
    )


def _machine(sata: Controller, *neighbours: PcieSlot, negotiated_gbps: float = 6.0) -> Inventory:
    """A machine where the controller sits in switch port 0000:08:0d.0, beside the given ports."""
    own_port = _port("0000:08:0d.0", occupant=sata.address, link=sata.link, occupant_class=_AHCI)
    return Inventory(
        "h",
        controllers=(sata,),
        disks=_drives(negotiated_gbps),
        slots=(_SWITCH_UPSTREAM_PORT, own_port, *neighbours),
    )


def _assert_judged_as_before(sata: Controller, machine: Inventory) -> None:
    """The oversubscription warning stands, and the slots changed nothing about it."""
    findings = diagnose_controller_oversubscription(sata, machine)

    assert len(findings) == 1, f"expected the oversubscription warning alone, got {findings}"
    assert findings[0].severity is Severity.WARNING
    assert "oversubscribed" in findings[0].title
    assert findings == diagnose_controller_oversubscription(sata, replace(machine, slots=())), (
        "the slot data altered a finding it must leave alone"
    )


@pytest.mark.os_agnostic
def test_a_second_function_at_the_floor_beside_a_real_link_is_not_read_as_a_ceiling() -> None:
    """The integrated-function pattern turns the warning into a hint that sends nobody shopping."""
    sata = _sata_controller()
    machine = _machine(sata, _USB_AT_THE_FLOOR, _NVME_WITH_A_REAL_LINK, _EMPTY_PORT)

    about_it = [finding for finding in diagnose(machine) if finding.subject == sata.address]

    assert not [f for f in about_it if "oversubscribed" in f.title], f"still called oversubscribed: {about_it}"
    assert len(about_it) == 1, f"expected one hint in place of the warning, got {about_it}"
    hint = about_it[0]
    assert hint.severity is Severity.HINT
    assert "PCIe floor" in hint.title
    assert "0000:10:00.0" in hint.detail, "the second function is the evidence, so the reader must be able to find it"
    assert hint.action is not None
    assert "specification" in hint.action
    assert _REPLACE_THE_CARD not in hint.action
    assert "wider slot" not in hint.action


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "neighbours",
    [
        pytest.param((_NVME_WITH_A_REAL_LINK, _EMPTY_PORT), id="no other function at the floor"),
        pytest.param(
            (_NVME_WITH_A_REAL_LINK, _USB_AT_THE_FLOOR_ON_ANOTHER_SWITCH),
            id="the only other floor sits on a different switch",
        ),
    ],
)
def test_a_floor_link_with_no_twin_on_its_switch_is_still_oversubscribed(neighbours: tuple[PcieSlot, ...]) -> None:
    """A genuinely narrow link reads the floor too, so without a twin it is taken at its word."""
    sata = _sata_controller()
    machine = _machine(sata, *neighbours)

    _assert_judged_as_before(sata, machine)
    assert _REPLACE_THE_CARD in (diagnose_controller_oversubscription(sata, machine)[0].action or "")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "neighbours",
    [
        pytest.param((_USB_AT_THE_FLOOR, _NVME_AT_THE_FLOOR), id="every occupant on the switch at the floor"),
        pytest.param(
            (_USB_AT_THE_FLOOR, _NVME_WITH_A_REAL_LINK_ON_ANOTHER_SWITCH),
            id="the only real link sits on a different switch",
        ),
    ],
)
def test_a_switch_with_nothing_above_the_floor_cannot_be_told_apart(neighbours: tuple[PcieSlot, ...]) -> None:
    """Where every device on the switch reads the floor, no reading says which of them is real."""
    sata = _sata_controller()
    machine = _machine(sata, *neighbours)

    _assert_judged_as_before(sata, machine)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "link",
    [
        pytest.param(PcieLink(5.0, 1, 5.0, 1), id="a real Gen2 x1 link"),
        pytest.param(PcieLink(2.5, 1, 5.0, 1), id="resting at the floor speed but capable of more"),
        pytest.param(PcieLink(2.5, 1, 2.5, 4), id="resting at one lane but capable of four"),
    ],
)
def test_a_controller_not_at_the_floor_in_both_is_judged_as_before(link: PcieLink) -> None:
    """Only a link pinned at the floor as both running and capable is a register default."""
    sata = _sata_controller(link)
    machine = _machine(sata, _USB_AT_THE_FLOOR, _NVME_WITH_A_REAL_LINK)

    _assert_judged_as_before(sata, machine)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("root_bus", "elsewhere"),
    [
        pytest.param("0000:00", (), id="root ports on bus 00"),
        pytest.param("0000:80", (), id="root ports on a second root bus at 80"),
        pytest.param("0000:00", _A_SWITCH_ELSEWHERE, id="root ports on bus 00 with a switch elsewhere"),
    ],
)
def test_the_pattern_across_root_ports_is_not_one_switch(root_bus: str, elsewhere: tuple[PcieSlot, ...]) -> None:
    """Ports on a root bus share a number, not a chip, so a floor beside one excuses nothing.

    A genuine Gen1 x1 SATA card in a root port reads exactly like an integrated
    function. An unrelated USB card at the floor in a second root port and a
    graphics card with a real link in a third complete the pattern if a shared
    bus number is taken for a shared switch, and the real bottleneck hides
    behind a hint. No bridge is recorded above a root bus, whatever its number,
    so nothing proves one switch and the warning stands.
    """
    sata = _sata_controller()
    graphics = PcieLink(16.0, 16, 16.0, 16)
    machine = Inventory(
        "h",
        controllers=(sata,),
        disks=_drives(),
        slots=(
            _port(f"{root_bus}:1c.4", occupant=sata.address, link=_FLOOR, occupant_class=_AHCI),
            _port(f"{root_bus}:1c.1", occupant="0000:06:00.0", link=_FLOOR, occupant_class=_USB),
            _port(f"{root_bus}:01.0", occupant="0000:01:00.0", link=graphics, occupant_class=_DISPLAY),
            *elsewhere,
        ),
    )

    _assert_judged_as_before(sata, machine)


@pytest.mark.os_agnostic
def test_a_floor_link_with_no_slot_data_is_judged_as_before() -> None:
    """With no ports read at all there is no twin to find, so the warning stands."""
    sata = _sata_controller()
    machine = Inventory("h", controllers=(sata,), disks=_drives())

    findings = diagnose_controller_oversubscription(sata, machine)

    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert _REPLACE_THE_CARD in (findings[0].action or "")


@pytest.mark.os_agnostic
def test_the_pattern_raises_nothing_where_no_warning_would_have_stood() -> None:
    """The hint replaces a warning; it is not a new remark on every integrated function.

    Three drives linked at 0.5 Gb/s want 0.15 GB/s together, under even the
    floor, so there was never anything to correct.
    """
    sata = _sata_controller()
    machine = _machine(sata, _USB_AT_THE_FLOOR, _NVME_WITH_A_REAL_LINK, negotiated_gbps=0.5)

    assert diagnose_controller_oversubscription(sata, machine) == []
    assert [finding for finding in diagnose(machine) if finding.subject == sata.address] == []
