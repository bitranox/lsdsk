"""Rules tests over hand-built machines.

The captured fixtures are healthy hardware, so most rules never fire on them.
These build the machine each rule is about, including the cases that must stay
silent: a rule that fires on everything is as useless as one that never fires.
"""

from __future__ import annotations

import pytest

from lsdsk.domain.diagnostics import (
    WEAR_CRITICAL_PERCENT,
    WEAR_WARNING_PERCENT,
    attached_demand_gbytes,
    count_by_severity,
    diagnose,
    diagnose_controller_link,
    diagnose_controller_oversubscription,
    diagnose_disk_link,
    diagnose_firmware_consistency,
    diagnose_health,
    diagnose_port_allocation,
    interface_demand_gbytes,
)
from lsdsk.domain.enums import BusType, ControllerKind, DiskKind, Severity
from lsdsk.domain.models import (
    Controller,
    Disk,
    Health,
    InterfaceLink,
    Inventory,
    PcieLink,
    PcieSlot,
    SmartAttribute,
)


def controller(
    address: str = "0000:03:00.0",
    *,
    link: PcieLink | None = None,
    upstream: PcieLink | None = None,
    name: str = "Test HBA",
    **kwargs: object,
) -> Controller:
    """Build a controller for one rule under test."""
    return Controller(
        address=address,
        name=name,
        kind=ControllerKind.SAS,
        link=link or PcieLink(8.0, 8, 8.0, 8),
        upstream=upstream,
        **kwargs,  # pyright: ignore[reportArgumentType] - test helper forwarding optional fields
    )


def disk(node: str = "sda", *, link: InterfaceLink | None = None, health: Health | None = None) -> Disk:
    """Build a disk for one rule under test."""
    return Disk(
        node=node,
        path=f"/dev/{node}",
        model="Test Drive",
        kind=DiskKind.SSD,
        bus=BusType.SATA,
        controller_address="0000:03:00.0",
        link=link or InterfaceLink(6.0, 6.0, 6.0),
        health=health,
    )


@pytest.mark.os_agnostic
def test_when_a_link_never_trained_it_is_critical() -> None:
    """Verify a device present at width zero is the most urgent case."""
    dead = controller(link=PcieLink(2.5, 0, 8.0, 8))

    findings = diagnose_controller_link(dead, Inventory("h", controllers=(dead,)))

    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert "never trained" in findings[0].title


@pytest.mark.os_agnostic
def test_when_a_link_negotiated_below_both_ends_it_is_actionable() -> None:
    """Verify a link below what both ends support is a warning, not a hint."""
    degraded = controller(link=PcieLink(8.0, 4, 8.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))

    findings = diagnose_controller_link(degraded, Inventory("h", controllers=(degraded,)))

    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].action is not None
    assert "riser" in findings[0].action


@pytest.mark.os_agnostic
def test_when_a_link_is_at_the_machine_ceiling_nothing_is_reported() -> None:
    """Verify a card running exactly as fast as the board allows stays silent."""
    matched = controller(link=PcieLink(8.0, 8, 8.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))

    assert diagnose_controller_link(matched, Inventory("h", controllers=(matched,))) == []


@pytest.mark.os_agnostic
def test_when_a_faster_free_slot_exists_the_finding_names_it() -> None:
    """Verify a real free slot turns the ceiling into an actionable move."""
    capped = controller(link=PcieLink(8.0, 8, 16.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))
    better = PcieSlot("0000:00:02.0", PcieLink(16.0, 16, 16.0, 16), occupied=False, connector_present=True)

    findings = diagnose_controller_link(capped, Inventory("h", controllers=(capped,), slots=(better,)))

    assert findings[0].severity is Severity.WARNING
    assert findings[0].action is not None
    assert "0000:00:02.0" in findings[0].action


@pytest.mark.os_agnostic
def test_when_the_only_faster_port_is_not_a_slot_it_is_not_offered() -> None:
    """Verify a port with no physical connector is never a move target.

    An internal port to a soldered-down device looks identical to a slot in the
    PCI topology; only the Slot Implemented bit distinguishes them.
    """
    capped = controller(link=PcieLink(8.0, 8, 16.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))
    internal = PcieSlot("0000:00:11.0", PcieLink(16.0, 16, 16.0, 16), occupied=False, connector_present=False)
    unknown = PcieSlot("0000:00:12.0", PcieLink(16.0, 16, 16.0, 16), occupied=False, connector_present=None)

    findings = diagnose_controller_link(capped, Inventory("h", controllers=(capped,), slots=(internal, unknown)))

    assert findings[0].severity is Severity.HINT
    assert "capped by the mainboard" in findings[0].title


@pytest.mark.os_agnostic
def test_when_a_wasteful_card_holds_a_faster_slot_a_swap_is_proposed() -> None:
    """Verify a card that cannot use its slot is offered as a trade."""
    capped = controller(link=PcieLink(8.0, 8, 16.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))
    nic_slot = PcieSlot(
        "0000:00:02.0",
        PcieLink(16.0, 16, 16.0, 16),
        occupied=True,
        connector_present=True,
        occupant_address="0000:02:00.0",
        occupant_class=0x020000,
        occupant_name="Gigabit Network Connection",
        occupant_link=PcieLink(2.5, 1, 2.5, 1),
    )

    findings = diagnose_controller_link(capped, Inventory("h", controllers=(capped,), slots=(nic_slot,)))

    assert findings[0].severity is Severity.WARNING
    assert findings[0].action is not None
    assert "Swap" in findings[0].action


@pytest.mark.os_agnostic
def test_when_the_faster_slot_holds_the_graphics_card_no_swap_is_proposed() -> None:
    """Verify a display controller is never proposed for displacement."""
    capped = controller(link=PcieLink(8.0, 8, 16.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))
    gpu_slot = PcieSlot(
        "0000:00:02.0",
        PcieLink(16.0, 16, 16.0, 16),
        occupied=True,
        connector_present=True,
        occupant_class=0x030000,
        occupant_link=PcieLink(2.5, 1, 2.5, 1),
    )

    findings = diagnose_controller_link(capped, Inventory("h", controllers=(capped,), slots=(gpu_slot,)))

    assert findings[0].severity is Severity.HINT


@pytest.mark.os_agnostic
def test_when_the_board_caps_a_card_the_hint_quantifies_the_upgrade() -> None:
    """Verify the platform-limited hint names a generation and a figure."""
    capped = controller(link=PcieLink(8.0, 8, 16.0, 8), upstream=PcieLink(8.0, 8, 8.0, 8))

    finding = diagnose_controller_link(capped, Inventory("h", controllers=(capped,)))[0]

    assert finding.severity is Severity.HINT
    assert finding.action is not None
    assert "PCIe 4.0 board" in finding.action
    assert "GB/s" in finding.action
    assert "speed" in finding.detail


@pytest.mark.os_agnostic
def test_a_board_with_faster_ports_in_use_is_not_told_to_buy_a_newer_board() -> None:
    """Verify an occupied fast port is not mistaken for an absent one.

    Measured on a MEG Z690 ACE: an NVMe drive sat in a Gen3 x4 port while the
    board's Gen4 x4 and Gen5 x8 ports were all occupied. Reading the current
    port as the board's ceiling recommended buying a PCIe 4.0 board to somebody
    who already owned a PCIe 5.0 one. The remedy is freeing a port, not buying.
    """
    capped = controller(link=PcieLink(8.0, 4, 16.0, 4), upstream=PcieLink(8.0, 4, 8.0, 4), name="NVMe")
    faster_but_taken = PcieSlot("0000:00:06.0", PcieLink(16.0, 4, 16.0, 4), occupied=True, connector_present=None)
    machine = Inventory("h", controllers=(capped,), slots=(faster_but_taken,))

    finding = diagnose_controller_link(capped, machine)[0]

    assert finding.severity is Severity.HINT
    assert finding.action is not None
    assert "PCIe 4.0 board" not in finding.action
    assert "the best this machine offers" not in finding.detail


@pytest.mark.os_agnostic
def test_when_the_board_really_has_nothing_faster_the_upgrade_stands() -> None:
    """Verify the board-upgrade advice survives where it is the only remedy."""
    capped = controller(link=PcieLink(8.0, 4, 16.0, 4), upstream=PcieLink(8.0, 4, 8.0, 4), name="NVMe")
    nothing_better = PcieSlot("0000:00:1c.0", PcieLink(8.0, 4, 8.0, 4), occupied=False, connector_present=None)
    machine = Inventory("h", controllers=(capped,), slots=(nothing_better,))

    finding = diagnose_controller_link(capped, machine)[0]

    assert finding.action is not None
    assert "PCIe 4.0 board" in finding.action


@pytest.mark.os_agnostic
def test_when_drives_outrun_the_uplink_it_is_oversubscribed() -> None:
    """Verify a controller whose drives exceed its uplink is reported."""
    narrow = controller(link=PcieLink(5.0, 1, 5.0, 1))
    drives = tuple(disk(f"sd{letter}") for letter in "abcdefgh")
    machine = Inventory("h", controllers=(narrow,), disks=drives)

    findings = diagnose_controller_oversubscription(narrow, machine)

    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert "oversubscribed" in findings[0].title


@pytest.mark.os_agnostic
def test_when_the_uplink_is_ample_nothing_is_reported() -> None:
    """Verify a controller with headroom stays silent."""
    wide = controller(link=PcieLink(16.0, 8, 16.0, 8))
    machine = Inventory("h", controllers=(wide,), disks=(disk(),))

    assert diagnose_controller_oversubscription(wide, machine) == []


@pytest.mark.os_agnostic
def test_when_a_drive_links_below_both_ends_it_is_a_warning() -> None:
    """Verify the cable-or-backplane case is actionable."""
    slow = disk(link=InterfaceLink(3.0, 6.0, 12.0))

    findings = diagnose_disk_link(slow, Inventory("h", disks=(slow,)))

    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].action is not None
    assert "cable" in findings[0].action


@pytest.mark.os_agnostic
def test_a_card_at_its_own_width_is_not_told_to_find_a_wider_slot() -> None:
    """Verify the remedy respects what the card itself can do.

    Measured on real hardware: an ASM1061 is a PCIe 2.0 x1 part, so its two
    SATA SSDs read 400 MB/s each alone and 206 MB/s each together, a shared
    ceiling at the uplink. Telling somebody to move a physically x1 card to a
    wider slot sends them to open a machine for a change the card cannot use.
    """
    at_own_ceiling = controller(link=PcieLink(5.0, 1, 5.0, 1), name="ASM1061")
    drives = tuple(disk(node, link=InterfaceLink(6.0, 6.0, 6.0)) for node in ("sde", "sdf"))
    machine = Inventory("h", controllers=(at_own_ceiling,), disks=drives)

    findings = diagnose_controller_oversubscription(at_own_ceiling, machine)

    assert len(findings) == 1
    assert findings[0].action is not None
    assert "move this card to a wider slot" not in findings[0].action
    assert "replace this card" in findings[0].action


@pytest.mark.os_agnostic
def test_a_card_below_its_own_width_may_be_told_to_move() -> None:
    """Verify the slot remedy survives where the card really could go wider."""
    narrowed = controller(link=PcieLink(5.0, 1, 8.0, 8), name="Wide HBA")
    drives = tuple(disk(node, link=InterfaceLink(6.0, 6.0, 6.0)) for node in ("sde", "sdf"))
    machine = Inventory("h", controllers=(narrowed,), disks=drives)

    findings = diagnose_controller_oversubscription(narrowed, machine)

    assert len(findings) == 1
    assert findings[0].action is not None
    assert "move this card to a wider slot" in findings[0].action


@pytest.mark.os_agnostic
def test_an_unread_port_capability_is_never_treated_as_fast_enough() -> None:
    """Verify an unknown end never stands in for a capable one.

    A drive linked at 3 Gb/s whose port capability was never read may simply be
    sitting in a 3 Gb/s port, which is no fault at all. Treating the unknown end
    as at least as fast as the drive turns a guess into a diagnosis.
    """
    half_known = InterfaceLink(3.0, 6.0, None)

    assert half_known.achievable_gbps is None
    assert not half_known.is_underperforming
    assert half_known.is_below_drive_capability


@pytest.mark.os_agnostic
def test_when_the_port_capability_is_unread_no_fault_is_claimed() -> None:
    """Verify a half-known link is surfaced without asserting a cable fault."""
    unknown_port = disk(link=InterfaceLink(3.0, 6.0, None))

    findings = diagnose_disk_link(unknown_port, Inventory("h", disks=(unknown_port,)))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity is Severity.WARNING
    assert "both ends" not in finding.title
    assert finding.action is not None
    assert "cable" not in finding.action


@pytest.mark.os_agnostic
def test_when_the_port_caps_the_drive_and_nothing_is_free_it_is_a_hint() -> None:
    """Verify a controller-limited drive with no better port is only a hint."""
    limited = disk(link=InterfaceLink(3.0, 6.0, 3.0))

    findings = diagnose_disk_link(limited, Inventory("h", disks=(limited,)))

    assert len(findings) == 1
    assert findings[0].severity is Severity.HINT
    assert findings[0].action is not None
    assert "host bus adapter" in findings[0].action


@pytest.mark.os_agnostic
def test_when_a_drive_runs_at_its_own_maximum_nothing_is_reported() -> None:
    """Verify a drive at capability is silent even on a much faster port."""
    fine = disk(link=InterfaceLink(6.0, 6.0, 12.0))

    assert diagnose_disk_link(fine, Inventory("h", disks=(fine,))) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("used", "expected"),
    [
        (10, None),
        (WEAR_WARNING_PERCENT - 1, None),
        (WEAR_WARNING_PERCENT, Severity.WARNING),
        (WEAR_CRITICAL_PERCENT, Severity.CRITICAL),
        (120, Severity.CRITICAL),
    ],
)
def test_wear_thresholds(used: int, expected: Severity | None) -> None:
    """Verify wear is graded at its documented thresholds and not before."""
    worn = disk(health=Health(percent_used=used))

    findings = [finding for finding in diagnose_health(worn) if "endurance" in finding.title]

    if expected is None:
        assert findings == []
    else:
        assert findings[0].severity is expected


@pytest.mark.os_agnostic
def test_when_a_drive_declares_itself_failing_it_is_critical() -> None:
    """Verify the drive's own overall verdict is taken seriously."""
    failing = disk(health=Health(ok=False))

    findings = [finding for finding in diagnose_health(failing) if "failing" in finding.title]

    assert findings[0].severity is Severity.CRITICAL


@pytest.mark.os_agnostic
def test_sector_counters_are_graded_by_what_they_mean() -> None:
    """Verify pending and uncorrectable outrank reallocated, which they do."""
    degraded = disk(health=Health(reallocated_sectors=8, pending_sectors=2, uncorrectable_sectors=1, media_errors=3))

    by_severity = {finding.severity for finding in diagnose_health(degraded)}
    titles = " ".join(finding.title for finding in diagnose_health(degraded))

    assert Severity.CRITICAL in by_severity
    assert "reallocated" in titles
    assert "pending" in titles
    assert "uncorrectable" in titles
    assert "media errors" in titles


@pytest.mark.os_agnostic
def test_when_counters_are_zero_nothing_is_reported() -> None:
    """Verify a healthy drive with all counters at zero stays silent."""
    healthy = disk(
        health=Health(ok=True, reallocated_sectors=0, pending_sectors=0, uncorrectable_sectors=0, media_errors=0)
    )

    assert diagnose_health(healthy) == []


@pytest.mark.os_agnostic
def test_temperature_is_graded_against_the_drives_own_limits() -> None:
    """Verify a drive's declared thresholds beat any generic band.

    An NVMe that declares 82 C warning is fine at 70 C, where a generic rule
    would have called it hot.
    """
    warm = disk(health=Health(temperature_c=70, temperature_warning_c=82, temperature_critical_c=85))
    over = disk(health=Health(temperature_c=83, temperature_warning_c=82, temperature_critical_c=85))
    critical = disk(health=Health(temperature_c=86, temperature_warning_c=82, temperature_critical_c=85))

    assert [f for f in diagnose_health(warm) if "C" in f.title] == []
    assert next(f for f in diagnose_health(over) if "warning threshold" in f.title).severity is Severity.WARNING
    assert next(f for f in diagnose_health(critical) if "critical limit" in f.title).severity is Severity.CRITICAL


@pytest.mark.os_agnostic
def test_when_a_disk_has_no_health_data_no_health_findings_are_made() -> None:
    """Verify an unprivileged run invents nothing."""
    assert diagnose_health(disk(health=None)) == []


@pytest.mark.os_agnostic
def test_mixed_firmware_is_reported_once_per_model() -> None:
    """Verify identical models on different revisions are flagged, and only those."""
    machine = Inventory(
        "h",
        disks=(
            Disk("sda", "/dev/sda", "Model A", firmware="1.0"),
            Disk("sdb", "/dev/sdb", "Model A", firmware="2.0"),
            Disk("sdc", "/dev/sdc", "Model B", firmware="9.0"),
            Disk("sdd", "/dev/sdd", "Model B", firmware="9.0"),
        ),
    )

    findings = diagnose_firmware_consistency(machine)

    assert len(findings) == 1
    assert findings[0].subject == "Model A"
    assert findings[0].severity is Severity.HINT


@pytest.mark.os_agnostic
def test_interface_demand_uses_the_pcie_link_for_nvme() -> None:
    """Verify an NVMe drive's demand comes from its PCIe link, not a serial rate."""
    nvme = Disk("nvme0n1", "/dev/nvme0n1", "NVMe", bus=BusType.NVME, pcie=PcieLink(8.0, 4, 8.0, 4))

    assert interface_demand_gbytes(nvme) == 3.94


@pytest.mark.os_agnostic
def test_attached_demand_sums_only_the_disks_on_that_controller() -> None:
    """Verify demand is attributed per controller, not machine-wide."""
    hba = controller()
    other = controller("0000:04:00.0")
    mine = disk("sda")
    theirs = Disk("sdb", "/dev/sdb", "m", controller_address="0000:04:00.0", link=InterfaceLink(6.0, 6.0, 6.0))
    machine = Inventory("h", controllers=(hba, other), disks=(mine, theirs))

    assert attached_demand_gbytes(hba, machine) == 0.6
    assert attached_demand_gbytes(other, machine) == 0.6


@pytest.mark.os_agnostic
def test_findings_are_sorted_most_urgent_first() -> None:
    """Verify the reader sees the worst thing first."""
    machine = Inventory(
        "h",
        controllers=(controller(link=PcieLink(2.5, 0, 8.0, 8)),),
        disks=(
            disk("sda", link=InterfaceLink(3.0, 6.0, 12.0)),
            disk("sdb", health=Health(percent_used=99)),
        ),
    )

    findings = diagnose(machine)
    severities = [finding.severity for finding in findings]

    assert severities == sorted(severities, key=lambda s: (s is not Severity.CRITICAL, s is not Severity.WARNING))
    assert severities[0] is Severity.CRITICAL


@pytest.mark.os_agnostic
def test_counting_by_severity_reports_every_level() -> None:
    """Verify the tally includes zeros, so a caller can format without guarding."""
    counts = count_by_severity(())

    assert set(counts) == set(Severity)
    assert all(value == 0 for value in counts.values())


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (None, "-"),
        (512, "512B"),
        (4096, "4.0K"),
        (500_107_862_016, "466G"),
        (4_000_787_030_016, "3.6T"),
        (20_000_000_000_000_000, "18P"),
    ],
)
def test_size_formatting(size_bytes: int | None, expected: str) -> None:
    """Verify capacities render the way a disk listing should read."""
    from lsdsk.adapters.render.theme import format_size

    assert format_size(size_bytes) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("port", "drive", "negotiated", "port_colour", "disk_colour", "link_colour"),
    [
        # A drive at its own maximum in a faster port: the drive is marked as
        # occupying a seat it cannot use, and nothing is called a fault.
        (12.0, 3.0, 3.0, "", "orange3", "green"),
        # Port and drive matched, running as agreed: nothing to say.
        (6.0, 6.0, 6.0, "", "", "green"),
        # The port is the constraint, so the port carries the warning.
        (3.0, 6.0, 3.0, "yellow", "", "green"),
        # Both ends could do more than the link achieved: a genuine fault.
        (6.0, 6.0, 3.0, "", "", "bold red"),
        (None, 6.0, 6.0, "dim", "dim", "green"),
        # The drive is below its own maximum but the port was never read, so a
        # 3 Gb/s port would explain it entirely. Yellow, because something is
        # off; not red, because red is reserved for a proven fault.
        (None, 6.0, 3.0, "dim", "dim", "yellow"),
    ],
)
def test_speed_column_colours(
    port: float | None,
    drive: float | None,
    negotiated: float | None,
    port_colour: str,
    disk_colour: str,
    link_colour: str,
) -> None:
    """Verify each speed column is coloured for its own kind of problem.

    Colour has to mean one thing per column, otherwise a drive that merely sits
    in a generous port looks identical to one whose link is broken.
    """
    from lsdsk.adapters.render.theme import disk_style, link_style, port_style

    assert port_style(port, drive) == port_colour
    assert disk_style(drive, port) == disk_colour
    assert link_style(negotiated, port, drive) == link_colour


@pytest.mark.os_agnostic
def test_temperature_without_declared_limits_falls_back_to_bands() -> None:
    """Verify a drive declaring no thresholds still gets a sane grading."""
    from lsdsk.adapters.render.theme import format_temperature

    assert format_temperature(30)[1] == "green"
    assert format_temperature(55)[1] == "yellow"
    assert format_temperature(65)[1] == "bold red"


@pytest.mark.os_agnostic
def test_markers_exist_for_every_severity() -> None:
    """Verify no severity can render without its colour-independent marker."""
    from lsdsk.adapters.render.theme import marker_for, style_for

    for severity in Severity:
        assert marker_for(severity), f"{severity} has no marker"
        assert style_for(severity), f"{severity} has no style"
    assert marker_for(None) == ""


@pytest.mark.os_agnostic
def test_when_a_slow_drive_holds_a_fast_port_a_swap_is_proposed() -> None:
    """Verify the wrong-seats case is found.

    Neither drive is faulty and neither would ever raise an alert, which is
    exactly how a machine ends up misallocated: an old drive occupies the fast
    port it cannot use while a fast drive runs at half speed elsewhere.
    """
    old = Disk("sda", "/dev/sda", "Old SATA-II drive", link=InterfaceLink(3.0, 3.0, 6.0))
    new = Disk("sdb", "/dev/sdb", "Modern SSD", link=InterfaceLink(3.0, 6.0, 3.0))

    findings = diagnose_port_allocation(Inventory("h", disks=(old, new)))

    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].subject == "/dev/sdb"
    assert findings[0].action is not None
    assert "Swap" in findings[0].action
    assert "/dev/sda" in findings[0].action


@pytest.mark.os_agnostic
def test_when_every_port_is_fast_enough_no_swap_is_proposed() -> None:
    """Verify a machine whose ports all exceed every drive stays silent.

    Wasted port capability is not a fault on its own. A 3 Gb/s drive on a 12 Gb/s
    phy is fine when there is no faster drive waiting for that phy.
    """
    machine = Inventory(
        "h",
        disks=(
            Disk("sda", "/dev/sda", "Old drive", link=InterfaceLink(3.0, 3.0, 12.0)),
            Disk("sdb", "/dev/sdb", "Modern SSD", link=InterfaceLink(6.0, 6.0, 12.0)),
        ),
    )

    assert diagnose_port_allocation(machine) == []


@pytest.mark.os_agnostic
def test_a_swap_is_not_proposed_when_the_partner_would_lose() -> None:
    """Verify a trade that merely moves the problem is refused."""
    starved = Disk("sda", "/dev/sda", "Fast", link=InterfaceLink(3.0, 6.0, 3.0))
    equally_fast = Disk("sdb", "/dev/sdb", "Also fast", link=InterfaceLink(6.0, 6.0, 6.0))

    assert diagnose_port_allocation(Inventory("h", disks=(starved, equally_fast))) == []


@pytest.mark.os_agnostic
def test_one_partner_is_not_promised_to_two_swaps() -> None:
    """Verify a single fast port is not offered to two starved drives at once."""
    machine = Inventory(
        "h",
        disks=(
            Disk("sda", "/dev/sda", "Fast A", link=InterfaceLink(3.0, 6.0, 3.0)),
            Disk("sdb", "/dev/sdb", "Fast B", link=InterfaceLink(3.0, 6.0, 3.0)),
            Disk("sdc", "/dev/sdc", "Old", link=InterfaceLink(3.0, 3.0, 6.0)),
        ),
    )

    findings = diagnose_port_allocation(machine)

    assert len(findings) == 1, "only one fast port is free, so only one swap can be promised"


# --------------------------------------------------------------------------
# The maker's own verdict on an attribute
# --------------------------------------------------------------------------


def _disk_with_failing_attribute() -> Disk:
    """A drive whose maker says one attribute has fallen below its limit."""
    failing = SmartAttribute(id=5, name="Reallocated_Sector_Ct", value=8, worst=8, threshold=10, raw=42)
    healthy = SmartAttribute(id=9, name="Power_On_Hours", value=95, worst=95, threshold=0, raw=1200)
    return Disk(node="sda", path="/dev/sda", model="ACME X", health=Health(attributes=(failing, healthy)))


@pytest.mark.os_agnostic
def test_an_attribute_below_its_makers_threshold_is_a_finding() -> None:
    """It used to be a red table row and nothing else.

    The condition never reached ``findings``, so it set no exit code, appeared
    in no summary, and disappeared entirely down a pipe or on a NO_COLOR
    terminal. It is the manufacturer's own verdict, which makes it the least
    arguable statement this tool can make about a drive.
    """
    findings = diagnose(Inventory("box", disks=(_disk_with_failing_attribute(),)))
    matching = [f for f in findings if "threshold" in f.title]
    assert matching, "the maker's own failing verdict raised no finding"
    assert matching[0].severity is Severity.CRITICAL
    assert matching[0].subject == "/dev/sda"


@pytest.mark.os_agnostic
def test_a_healthy_attribute_table_raises_nothing() -> None:
    """The control: the rule must not fire on every drive that has attributes."""
    healthy = SmartAttribute(id=9, name="Power_On_Hours", value=95, worst=95, threshold=0, raw=1200)
    disk = Disk(node="sdb", path="/dev/sdb", model="ACME Y", health=Health(attributes=(healthy,)))
    findings = diagnose(Inventory("box", disks=(disk,)))
    assert not [f for f in findings if "threshold" in f.title], "the rule fired on a healthy attribute table"


@pytest.mark.os_agnostic
def test_the_failing_attribute_row_carries_a_text_marker() -> None:
    """Colour is never the only carrier, which theme.py states as a law.

    Rendered with colour off, exactly as a pipe or NO_COLOR sees it, the failing
    row has to be distinguishable from the healthy one.
    """
    import io

    from rich.console import Console

    from lsdsk.adapters.render.report import render_smart

    buffer = io.StringIO()
    console = Console(file=buffer, width=100, no_color=True, force_terminal=False)
    console.print(render_smart(Inventory("box", disks=(_disk_with_failing_attribute(),)), 100))
    rendered = buffer.getvalue()
    failing_line = next(line for line in rendered.splitlines() if "Reallocated_Sector_Ct" in line)
    healthy_line = next(line for line in rendered.splitlines() if "Power_On_Hours" in line)
    assert "!!" in failing_line, "the failing row is indistinguishable without colour"
    assert "!!" not in healthy_line, "the marker is on every row, so it marks nothing"
