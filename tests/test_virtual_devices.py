"""Kernel-virtual block devices: classified, kept apart, and shown on request.

A zram, loop or zvol node is part of the machine and has no transport, no
counters and no port. Both halves of that matter. Dropping it made hardware the
machine really has vanish from its own inventory; letting it into the physical
list made a device with nothing to say fail every check that asks a drive a
question.

So it is kept in its own list. `Inventory.disks` stays the physical drives every
rule and every reading is about, and `Inventory.virtual_disks` carries the rest,
which the tree and the disk table show as a summary line unless asked to list
them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report, tables
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import BusType, DiskKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

_PCI_PATH = "/sys/devices/pci0000:00/0000:00:17.0/ata5/host4/target4:0:0/4:0:0:0"


def _capture() -> dict[str, Any]:
    """A Linux capture holding one real disk and two kernel-virtual devices."""
    return {
        "platform": "linux",
        "hostname": "example",
        "block": {
            "sda": {
                "size": "1024",
                "queue": {"rotational": "0"},
                "device": {"model": "A Model"},
                "device_path": _PCI_PATH,
            },
            "zram0": {"size": "2048", "queue": {"rotational": "0"}, "virtual": True},
            "loop0": {"size": "8", "queue": {"rotational": "0"}, "virtual": True},
        },
    }


@pytest.mark.os_agnostic
class TestTheInventoryKeepsThemApart:
    """Which list a device lands in is what every later rule reads."""

    def test_a_physical_disk_is_a_disk(self) -> None:
        assert [disk.node for disk in build_from(_capture()).disks] == ["sda"]

    def test_a_virtual_device_is_not_in_the_physical_list(self) -> None:
        assert "zram0" not in [disk.node for disk in build_from(_capture()).disks]

    def test_every_virtual_device_is_carried_separately(self) -> None:
        assert [disk.node for disk in build_from(_capture()).virtual_disks] == ["loop0", "zram0"]

    def test_a_virtual_device_reports_no_media_kind(self) -> None:
        """`rotational` is 0 for loop, zd and zram alike, and means nothing there.

        It is a default the kernel fills in for a device with no media, not a
        reading of any. Mapping it made a zvol on a pool of spinning disks
        report SSD, which is the tool inventing a fact it never measured.
        """
        kinds = {disk.kind for disk in build_from(_capture()).virtual_disks}
        assert kinds == {DiskKind.UNKNOWN}

    def test_a_virtual_device_says_which_bus_it_is_on(self) -> None:
        """`unknown` reads as "could not be determined", which is wrong here.

        Nothing failed to be read: the kernel says this device has no transport,
        and `virtual` says exactly that.
        """
        buses = {disk.bus for disk in build_from(_capture()).virtual_disks}
        assert buses == {BusType.VIRTUAL}


@pytest.mark.os_agnostic
class TestNoRuleJudgesAVirtualDevice:
    """A device with no link and no SMART must raise nothing, ever."""

    def test_no_finding_names_a_virtual_device(self) -> None:
        findings = diagnose(build_from(_capture()))
        virtual_paths = {disk.path for disk in build_from(_capture()).virtual_disks}
        assert not [finding for finding in findings if finding.subject in virtual_paths]


def _capture_without_virtual_devices() -> dict[str, Any]:
    """The same machine with nothing but its real disk."""
    capture = _capture()
    capture["block"] = {"sda": capture["block"]["sda"]}
    return capture


@pytest.mark.os_agnostic
class TestTheSummaryLine:
    """What a reader is told about devices that were not listed."""

    def test_it_counts_each_family(self) -> None:
        note = report.virtual_note(build_from(_capture()).virtual_disks)
        assert "1 loop" in note
        assert "1 zram" in note

    def test_it_says_how_to_see_them(self) -> None:
        assert "--expand-virtual" in report.virtual_note(build_from(_capture()).virtual_disks)


@pytest.mark.os_agnostic
class TestTheTreeCollapsesThemByDefault:
    """The default page is the whole machine at a glance, so it stays readable."""

    def test_the_tree_says_they_are_there(self, rendered: Callable[..., str]) -> None:
        text = rendered(report.render_tree(build_from(_capture()), ()))
        assert "1 loop" in text

    def test_the_tree_does_not_list_them(self, rendered: Callable[..., str]) -> None:
        """A host with forty zvols would otherwise bury its real drives."""
        text = rendered(report.render_tree(build_from(_capture()), ()))
        assert "zram0" not in text

    def test_expanding_lists_every_one(self, rendered: Callable[..., str]) -> None:
        text = rendered(report.render_tree(build_from(_capture()), (), expand_virtual=True))
        assert "/dev/zram0" in text
        assert "/dev/loop0" in text

    def test_a_machine_without_any_says_nothing_about_them(self, rendered: Callable[..., str]) -> None:
        text = rendered(report.render_tree(build_from(_capture_without_virtual_devices()), ()))
        assert "virtual" not in text.lower()


@pytest.mark.os_agnostic
class TestTheDiskTableAgreesWithTheTree:
    """One rule for what is shown, so two views cannot disagree about it."""

    def test_the_table_says_what_it_left_out(self, rendered: Callable[..., str]) -> None:
        text = rendered(tables.render_disks(build_from(_capture()), ()))
        assert "1 zram" in text
        assert "/dev/zram0" not in text

    def test_the_table_lists_them_when_expanded(self, rendered: Callable[..., str]) -> None:
        text = rendered(tables.render_disks(build_from(_capture()), (), expand_virtual=True))
        assert "/dev/zram0" in text


@pytest.mark.os_agnostic
class TestTheHeaderCountsThemApart:
    """The count at the top must not quietly fold them into the drive count."""

    def test_the_drive_count_excludes_them(self, rendered: Callable[..., str]) -> None:
        text = rendered(report.render_header(build_from(_capture())))
        assert "1 disks" in text

    def test_the_header_still_mentions_them(self, rendered: Callable[..., str]) -> None:
        text = rendered(report.render_header(build_from(_capture())))
        assert "2 virtual" in text


FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json"


def _run(runner: CliRunner, factory: Callable[[], Any], *args: str) -> str:
    """Drive the real CLI over the capture that carries virtual devices."""
    result = runner.invoke(cli_mod.cli, [*args, "--replay", str(FIXTURE)], obj=factory)
    assert result.exit_code in (0, 1), result.output
    return result.output


@pytest.mark.os_agnostic
class TestTheFlagReachesEveryViewThatLists:
    """A reader who sees the tally must be able to type what it suggests."""

    @pytest.mark.parametrize("command", ["disks", "topology"])
    def test_the_devices_are_tallied_by_default(
        self, command: str, cli_runner: CliRunner, production_factory: Callable[[], Any]
    ) -> None:
        assert "1 zram" in _run(cli_runner, production_factory, command)

    @pytest.mark.parametrize("command", ["disks", "topology"])
    def test_the_subcommand_takes_the_flag_the_tally_names(
        self, command: str, cli_runner: CliRunner, production_factory: Callable[[], Any]
    ) -> None:
        """The caption says --expand-virtual, so typing it there must work."""
        assert "/dev/zram0" in _run(cli_runner, production_factory, command, "--expand-virtual")

    @pytest.mark.parametrize("command", ["disks", "topology"])
    def test_the_flag_works_before_the_subcommand_too(
        self, command: str, cli_runner: CliRunner, production_factory: Callable[[], Any]
    ) -> None:
        result = cli_runner.invoke(
            cli_mod.cli,
            ["--expand-virtual", "--replay", str(FIXTURE), command],
            obj=production_factory,
        )
        assert result.exit_code in (0, 1), result.output
        assert "/dev/zram0" in result.output

    def test_the_configuration_key_does_the_same(
        self, cli_runner: CliRunner, production_factory: Callable[[], Any]
    ) -> None:
        """A machine whose zvols are the point should not need the flag every time."""
        assert "/dev/zram0" in _run(cli_runner, production_factory, "--set", "display.expand_virtual=true", "disks")


@pytest.mark.os_agnostic
class TestTheEnvelopeCarriesThem:
    """A consumer parsing JSON must see the whole machine, tally or not."""

    def test_the_virtual_devices_are_their_own_field(
        self, cli_runner: CliRunner, production_factory: Callable[[], Any]
    ) -> None:
        payload = json.loads(_run(cli_runner, production_factory, "disks", "--format", "json"))
        nodes = [disk["node"] for disk in payload["data"]["virtual_disks"]]
        assert nodes == ["loop0", "zd0", "zram0"]

    def test_they_are_not_mixed_into_the_drives(
        self, cli_runner: CliRunner, production_factory: Callable[[], Any]
    ) -> None:
        payload = json.loads(_run(cli_runner, production_factory, "disks", "--format", "json"))
        assert "zram0" not in [disk["node"] for disk in payload["data"]["disks"]]


@pytest.mark.os_agnostic
@pytest.mark.asyncio
class TestTheTuiAgreesWithThePrintedPage:
    """Key 3 and `lsdsk disks` are one view, so they list the same devices."""

    async def test_the_disk_page_tallies_them_by_default(self) -> None:
        machine = build_from(_capture())
        app = LsdskApp(machine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert rows_of(app.query_one("#disk-table")).row_count == len(machine.disks)

    async def test_the_disk_page_lists_them_when_expanded(self) -> None:
        machine = build_from(_capture())
        app = LsdskApp(machine, display=DisplaySettings(expand_virtual=True))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            expected = len(machine.disks) + len(machine.virtual_disks)
            assert rows_of(app.query_one("#disk-table")).row_count == expected
