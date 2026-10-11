"""A link figure that is a register default is marked as one, in every view that draws it.

A desktop chipset used as a PCIe switch publishes the PCIe floor, 2.5 GT/s x1,
as both running and capable on the SATA and USB functions built into it,
whatever its real uplink is. The findings already turn the oversubscription
warning into a hint for such a controller. The views went on stating the
figure: the tables and the tree drew ``Gen1x1 (0.25 GB/s)`` as the link's
worth, and the detail panel claimed ``uplink carries <= 0.25 GB/s`` directly
above the hint saying that figure is not a ceiling. The committed capture of a
board with two such chips daisy-chained is the user's own complaint: the drive
on the first chip's SATA function delivers about 500 MB/s.

The decision is the user's, verbatim: wherever the floor hint applies, the link
cells get a marker with a legend ("register default, not the real link"), the
same way the existing markers work, and the panel stops claiming the uplink
carries that figure. The JSON keeps the raw register values. It must not mark
a floor link the hint does not excuse, so every arm here has a control that
must keep drawing the plain figure with what it is worth.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import detail, report, tables, theme
from lsdsk.adapters.render.tree import FabricView, render_fabric
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import ControllerKind, Environment, TreeDensity
from lsdsk.domain.models import Controller, Inventory, PcieLink, PcieSlot
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CHIPSET = "windows-chipset-switch"

#: The two chipset SATA functions the floor evidence excuses: the first chip's,
#: which carries the drive the user measured, and the second chip's, which has
#: no drive. The marker is about what the register IS, so it does not wait for
#: a drive to make the figure matter.
EXCUSED = ("0000:10:00.0", "0000:0e:00.0")

#: The port in front of each, whose occupant link is the controller's own.
EXCUSED_PORTS = ("0000:04:0d.0", "0000:07:0d.0")

#: What the floor is worth, which is the figure no view may present as the link's.
FLOOR_WORTH = "0.25 GB/s"

#: How every excused figure reads once marked.
MARKED = f"Gen1x1{theme.REGISTER_DEFAULT}" if hasattr(theme, "REGISTER_DEFAULT") else "Gen1x1<marker>"

MEANING = "register default, not the real link"


def _machine(host: str = CHIPSET) -> Inventory:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _without_real_links(machine: Inventory) -> Inventory:
    """The same board with every port that runs above the floor taken away.

    The third reading the hint needs is a real link on or beneath the switch.
    Without one, a floor link reads exactly like a dead or downtrained one, so
    the hint no longer excuses it - and neither may the marker.
    """
    kept = tuple(
        slot
        for slot in machine.slots
        if not (slot.occupant_link is not None and slot.occupant_link.is_running_above_floor)
    )
    return machine.with_changes(slots=kept)


def _drawn(renderable: object, width: int = 200) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _line_of(page: str, address: str) -> str:
    lines = [line for line in page.splitlines() if address in line]
    assert lines, f"no line names {address}, so nothing below asserts anything"
    return lines[0]


def _controller(machine: Inventory, address: str) -> Controller:
    return next(one for one in machine.controllers if one.address == address)


def _values(record: detail.Detail, label: detail.DetailGroupLabel) -> dict[str, str]:
    """One group's values by name: two groups of one panel can both say ``running``."""
    group = next(one for one in record.groups if one.label is label)
    return {name: text for name, (text, _style) in group.values}


def test_the_decider_excuses_exactly_the_two_chipset_functions_and_no_other_controller() -> None:
    """One decider, read by every view: the two SATA functions here, nothing anywhere else.

    The second chip's function has no drive, so the hint never fires for it;
    the marker is still drawn, because it says what the register is rather
    than that something needs it.
    """
    from lsdsk.domain.diagnostics import register_default_links

    assert register_default_links(_machine()) == frozenset(EXCUSED)
    for host in sorted(path.stem for path in FIXTURES.glob("*.json")):
        if host == CHIPSET:
            continue
        assert register_default_links(_machine(host)) == frozenset(), f"{host}: a controller was marked"


def test_the_decider_marks_nothing_once_the_switch_shows_no_real_link() -> None:
    """The control the hint itself has: every link on and beneath the switch at the floor."""
    from lsdsk.domain.diagnostics import link_is_register_default, register_default_links

    stripped = _without_real_links(_machine())
    assert register_default_links(stripped) == frozenset()
    assert not any(link_is_register_default(one, stripped) for one in stripped.controllers)


def test_the_decider_marks_nothing_where_the_readings_are_not_physical() -> None:
    """In a virtual machine the link rules do not run, so no hint can excuse a figure there."""
    from lsdsk.domain.diagnostics import register_default_links

    virtual = _machine().with_changes(environment=Environment.VIRTUAL_MACHINE)
    assert not virtual.readings_are_physical, "the control: this is still read as physical hardware"
    assert register_default_links(virtual) == frozenset()


@pytest.mark.parametrize("address", EXCUSED)
def test_the_controllers_table_does_not_price_a_register_default(address: str) -> None:
    """The running and capable cells carry the marker and no bandwidth; the table explains it."""
    machine = _machine()
    page = _drawn(tables.render_controllers(machine, diagnose(machine), width=200))
    line = _line_of(page, address)
    assert FLOOR_WORTH not in line, f"the controllers table priced a register default: {line!r}"
    assert line.count(MARKED) == 2, f"running and capable must both carry the marker: {line!r}"
    assert f"{theme.REGISTER_DEFAULT} = {MEANING}" in page, "the marker was drawn and never explained"


def test_the_controllers_table_keeps_the_plain_figure_where_the_hint_does_not_excuse_it() -> None:
    """The control: the same floor link with no real link on the switch is a real figure."""
    machine = _without_real_links(_machine())
    page = _drawn(tables.render_controllers(machine, diagnose(machine), width=200))
    line = _line_of(page, EXCUSED[0])
    assert line.count(f"Gen1x1 ({FLOOR_WORTH})") == 2, line
    assert MEANING not in page


@pytest.mark.parametrize("address", EXCUSED)
def test_the_topology_tree_does_not_price_a_register_default(address: str) -> None:
    """Both hop columns carry the marker, in both tiers, and the section's legend names it."""
    machine = _machine()
    for width in (200, 90):
        page = _drawn(
            render_fabric(
                machine, diagnose(machine), width, FabricView(density=TreeDensity.FULL), thresholds=DEFAULT_THRESHOLDS
            ),
            width,
        )
        line = _line_of(page, address)
        assert FLOOR_WORTH not in line, f"the tree priced a register default at width {width}: {line!r}"
        assert line.count(MARKED) == 2, f"width {width}: both hop columns must carry the marker: {line!r}"
        assert f"{theme.REGISTER_DEFAULT} = {MEANING}" in page, f"width {width}: the marker was never explained"


def test_the_topology_tree_keeps_the_plain_figure_where_the_hint_does_not_excuse_it() -> None:
    """The control: with no real link on the switch the hop is a figure, worth included."""
    machine = _without_real_links(_machine())
    page = _drawn(
        render_fabric(
            machine, diagnose(machine), 200, FabricView(density=TreeDensity.FULL), thresholds=DEFAULT_THRESHOLDS
        )
    )
    assert _line_of(page, EXCUSED[0]).count(f"Gen1x1 ({FLOOR_WORTH})") == 2
    assert MEANING not in page


@pytest.mark.parametrize("address", EXCUSED)
def test_the_controller_panel_neither_prices_the_link_nor_claims_its_ceiling(address: str) -> None:
    """The link group carries the marker, the uplink names no ceiling, and the legend explains it.

    Asked of the panel's VALUES rather than its whole text: the findings drawn
    under them quote the register on purpose, which is how the hint explains
    itself.
    """
    machine = _machine()
    record = detail.controller_detail(_controller(machine, address), machine)
    link = _values(record, detail.LINK)
    uplink = _values(record, detail.PORTS)["uplink carries"]
    assert FLOOR_WORTH not in link["running"] and FLOOR_WORTH not in link["capable"], link
    assert uplink == theme.NOT_READ, f"the panel still claims a ceiling the register cannot give: {uplink!r}"
    assert link["running"] == MARKED and link["capable"] == MARKED, link
    panel = _drawn(detail.render_detail(record, diagnose(machine)))
    assert detail.REGISTER_DEFAULT_LEGEND in panel, "the marker was drawn and never explained"
    assert f"uplink carries {theme.AT_MOST}" not in panel


def test_the_controller_panel_keeps_its_figures_where_the_hint_does_not_excuse_them() -> None:
    """The control: no marker, the figure with its worth, and the uplink it rests on."""
    machine = _without_real_links(_machine())
    record = detail.controller_detail(_controller(machine, EXCUSED[0]), machine)
    link = _values(record, detail.LINK)
    assert link["running"] == link["capable"] == f"Gen1x1 ({FLOOR_WORTH})", link
    assert _values(record, detail.PORTS)["uplink carries"] == f"{theme.AT_MOST} {FLOOR_WORTH}"
    assert MEANING not in _drawn(detail.render_detail(record, diagnose(machine)))


@pytest.mark.parametrize("port", EXCUSED_PORTS)
def test_the_slots_table_does_not_price_a_register_default(port: str) -> None:
    """What the occupant needs is the register's figure, so it carries the marker and no worth."""
    page = _drawn(report.render_slots(_machine(), width=200))
    line = _line_of(page, port)
    assert FLOOR_WORTH not in line, f"the slots table priced a register default: {line!r}"
    assert MARKED in line, line
    assert f"{theme.REGISTER_DEFAULT} = {MEANING}" in page, "the marker was drawn and never explained"


def test_the_slots_table_keeps_a_floor_link_no_hint_excuses() -> None:
    """The control on another board: a device at the floor that is no excused controller.

    ``linux-usb-ehci`` has a port whose occupant reads 2.5 GT/s x1 and is not a
    function of any switch the evidence can show, so its figure is a real one.
    """
    page = _drawn(report.render_slots(_machine("linux-usb-ehci"), width=200))
    assert f"Gen1x1 ({FLOOR_WORTH})" in _line_of(page, "0000:00:1c.7")
    assert MEANING not in page


@pytest.mark.parametrize("port", EXCUSED_PORTS)
def test_the_slot_panel_does_not_price_a_register_default(port: str) -> None:
    """The socket's panel names the occupant's need with the marker, and explains it."""
    machine = _machine()
    slot = next(one for one in machine.slots if one.address == port)
    record = detail.slot_detail(slot, machine)
    needs = _values(record, detail.OCCUPANT)["needs"]
    assert FLOOR_WORTH not in needs, f"the slot panel priced a register default: {needs!r}"
    assert needs == MARKED, needs
    assert detail.REGISTER_DEFAULT_LEGEND in _drawn(detail.render_detail(record, diagnose(machine)))


def test_the_disk_and_controller_tree_says_the_figure_is_a_register_default() -> None:
    """The fallback tree speaks in prose, so it says so in words rather than a column marker."""
    machine = _machine()
    page = _drawn(report.render_controller_disks(machine, diagnose(machine), 200, thresholds=DEFAULT_THRESHOLDS), 200)
    line = _line_of(page, EXCUSED[0])
    assert MEANING in line, f"the old tree drew a register default as the link: {line!r}"
    control = _without_real_links(machine)
    plain = _drawn(report.render_controller_disks(control, diagnose(control), 200, thresholds=DEFAULT_THRESHOLDS))
    assert MEANING not in plain


@pytest.mark.asyncio
async def test_the_interactive_pages_do_not_price_a_register_default() -> None:
    """The controllers and slots pages build their rows from the printed tables' own builders."""
    machine = _machine()
    app = LsdskApp(machine)
    async with app.run_test(size=(200, 45)) as pilot:
        await pilot.pause()
        controller_row = " ".join(
            cell.plain for cell in rows_of(app.query_one("#controller-table")).get_row(EXCUSED[0])
        )
        slot_row = " ".join(cell.plain for cell in rows_of(app.query_one("#slot-table")).get_row(EXCUSED_PORTS[0]))
    assert FLOOR_WORTH not in controller_row, controller_row
    assert FLOOR_WORTH not in slot_row, slot_row
    assert controller_row.count(MARKED) == 2, controller_row
    assert MARKED in slot_row, slot_row


@pytest.mark.parametrize("command", ["controllers", "slots", "topology"])
def test_the_json_keeps_the_raw_register_values(
    command: str, cli_runner: CliRunner, production_factory: Callable[[], AppServices], tmp_path: Path
) -> None:
    """The marker is a view's; a program reading the envelope gets the register as it was read."""
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(
        cli,
        [
            "--replay",
            str(FIXTURES / f"{CHIPSET}.json"),
            "--history-file",
            str(tmp_path / "history.json"),
            "--no-record",
            command,
            "--format",
            "json",
        ],
        obj=production_factory,
    )
    envelope = json.loads(result.stdout)
    controllers = {one["address"]: one for one in envelope["data"]["controllers"]}
    for address in EXCUSED:
        link = controllers[address]["link"]
        assert (link["current_speed_gtps"], link["current_width"], link["max_speed_gtps"], link["max_width"]) == (
            2.5,
            1,
            2.5,
            1,
        ), link
    assert theme.REGISTER_DEFAULT not in json.dumps(envelope["data"]["controllers"])


_FLOOR = PcieLink(current_speed_gtps=2.5, current_width=1, max_speed_gtps=2.5, max_width=1)
_CHIPSET_MAKER = 0x1022


def _read_port_machine(*, real_link: bool) -> Inventory:
    """A switch whose ports WERE read, as Linux reads them, with a chipset SATA function behind one.

    The port in front of the function reads the floor as its own capability,
    which is what an internal port of a switch publishes, so the function is
    not ruled out. With ``real_link`` a neighbour on the switch runs above the
    floor and the hint excuses the function; without it nothing does.
    """
    floor_port = PcieLink(current_speed_gtps=2.5, current_width=1, max_speed_gtps=2.5, max_width=1)

    def port(address: str, occupant: str, link: PcieLink, occupant_class: int) -> PcieSlot:
        return PcieSlot(
            address=address,
            link=floor_port,
            occupied=True,
            occupant_address=occupant,
            occupant_class=occupant_class,
            occupant_link=link,
            vendor=_CHIPSET_MAKER,
            occupant_vendor=_CHIPSET_MAKER,
        )

    sata = Controller(
        address="0000:11:00.0",
        name="Chipset SATA Controller",
        kind=ControllerKind.AHCI,
        link=_FLOOR,
        upstream=floor_port,
        upstream_address="0000:08:0d.0",
        vendor=_CHIPSET_MAKER,
    )
    fast = PcieLink(current_speed_gtps=16.0, current_width=4, max_speed_gtps=16.0, max_width=4)
    neighbour = fast if real_link else _FLOOR
    slots = (
        PcieSlot(address="0000:07:00.0", link=PcieLink(), occupied=True, occupant_address="0000:08:00.0"),
        port("0000:08:0d.0", sata.address, _FLOOR, 0x010601),
        port("0000:08:0c.0", "0000:10:00.0", _FLOOR, 0x0C0330),
        port("0000:08:00.0", "0000:09:00.0", neighbour, 0x010802),
    )
    return Inventory(hostname="h", controllers=(sata,), slots=slots)


def test_a_read_port_holding_a_register_default_is_not_judged_full_by_it() -> None:
    """Where the port in front WAS read, a verdict and an uplink built from the default go too.

    The port reads the floor and so does its occupant, so the slots table
    called the port ``full`` and the panel said the uplink carries 0.25 GB/s
    with both ends read - two claims resting on the same register default the
    hint withdraws. The control drops the neighbour's real link, so nothing
    excuses the floor and both claims stand.
    """
    machine = _read_port_machine(real_link=True)
    line = _line_of(_drawn(report.render_slots(machine, width=200)), "0000:08:0d.0")
    assert "full" not in line, f"the slots table weighed the port against a register default: {line!r}"
    assert "in use" in line and MARKED in line, line
    sata = machine.controllers[0]
    uplink = _values(detail.controller_detail(sata, machine), detail.PORTS)["uplink carries"]
    assert uplink == theme.NOT_READ, f"the panel built an uplink from the register default: {uplink!r}"

    control = _read_port_machine(real_link=False)
    plain = _line_of(_drawn(report.render_slots(control, width=200)), "0000:08:0d.0")
    assert "full" in plain and f"Gen1x1 ({FLOOR_WORTH})" in plain, plain
    assert _values(detail.controller_detail(control.controllers[0], control), detail.PORTS)["uplink carries"] == (
        FLOOR_WORTH
    )


def test_a_table_explains_the_mark_exactly_when_a_column_it_drew_carries_one() -> None:
    """The legend follows what survived the width, never what the rows could have drawn.

    A narrow terminal gives up the link columns, and a legend for a mark nobody
    has on screen is noise; a wide one keeps them, and a mark with no legend is
    a symbol the reader has to guess. Swept over every width so both arms occur.
    """
    machine = _machine()
    findings = diagnose(machine)
    arms: set[bool] = set()
    for width in range(20, 201):
        for page in (
            _drawn(tables.render_controllers(machine, findings, width=width), width),
            _drawn(report.render_slots(machine, width=width), width),
        ):
            explained = MEANING in " ".join(page.split())
            drawn = MARKED in page
            arms.add(drawn)
            assert explained == drawn, f"width {width}: mark drawn {drawn}, legend drawn {explained}\n{page}"
    assert arms == {True, False}, f"the sweep never reached both arms: {arms}"
