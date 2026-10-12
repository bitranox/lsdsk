"""A SAS controller's used ports are the host phys carrying a link, never the disks behind them.

An HBA with eight host phys and twenty drives behind one expander on one phy has
seven ports free, not none; a two-phy wide port to an expander holding one drive
uses two phys. Counting disks per controller got both wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lsdsk.adapters.hw.snapshot import build_from

if TYPE_CHECKING:
    from lsdsk.domain.models import Controller

_ADDRESS = "0000:03:00.0"
_HBA = f"/sys/devices/pci0000:00/0000:00:03.0/{_ADDRESS}"


def _host_phy(number: int, *, port: str | None) -> dict[str, str]:
    """One host phy that trained a link, optionally recorded as a member of ``port``."""
    entry = {
        "path": f"{_HBA}/host6/phy-6:{number}/sas_phy/phy-6:{number}",
        "negotiated_linkrate": "6.0 Gbit",
        "maximum_linkrate_hw": "12.0 Gbit",
    }
    if port is not None:
        entry["port"] = port
    return entry


def _drive_behind_expander(index: int) -> dict[str, Any]:
    """A SAS drive on expander phy ``index``, below the HBA's port ``port-6:0``."""
    path = f"{_HBA}/host6/port-6:0/expander-6:0/port-6:0:{index}/end_device-6:0:{index}/target6:0:{index}/6:0:{index}:0"
    return {"size": "7814037168", "device_path": path, "device": {"model": "X", "vendor": "SEAGATE"}, "vpd": {}}


def _expander_phy(number: int) -> dict[str, str]:
    """One phy of the expander below ``port-6:0``, which the kernel also links to that port."""
    name = f"phy-6:0:{number}"
    return {
        "path": f"{_HBA}/host6/port-6:0/expander-6:0/{name}/sas_phy/{name}",
        "negotiated_linkrate": "6.0 Gbit",
        "maximum_linkrate_hw": "12.0 Gbit",
        "port": "port-6:0",
    }


def _controller(*, host_phys: int, wide_members: int, drives: int, expander_phys: int = 0) -> Controller:
    """The one HBA of a capture: ``host_phys`` host phys, the first ``wide_members`` forming ``port-6:0``."""
    phys = {f"phy-6:{n}": _host_phy(n, port="port-6:0" if n < wide_members else None) for n in range(host_phys)}
    phys.update({f"phy-6:0:{n}": _expander_phy(n) for n in range(expander_phys)})
    reading = {
        "schema": 2,
        "platform": "linux",
        "hostname": "sas",
        "kernel": "6.8.0",
        "pci": {_ADDRESS: {"class": "0x010700", "driver": "mpt3sas", "path": _HBA}},
        "classes": {"sas_phy": phys},
        "block": {f"sdzz{n}": _drive_behind_expander(n) for n in range(drives)},
    }
    (controller,) = build_from(reading).controllers
    return controller


def test_twenty_drives_behind_one_expander_on_one_phy_use_one_of_eight_ports() -> None:
    controller = _controller(host_phys=8, wide_members=1, drives=20)

    assert (controller.port_count, controller.ports_used, controller.ports_free) == (8, 1, 7)


def test_a_two_phy_wide_port_to_an_expander_with_one_drive_uses_two_of_sixteen_phys() -> None:
    controller = _controller(host_phys=16, wide_members=2, drives=1)

    assert (controller.port_count, controller.ports_used, controller.ports_free) == (16, 2, 14)


def test_a_port_whose_phys_are_not_recorded_counts_as_one_used_phy_however_many_drives_hang_off_it() -> None:
    """The committed captures record no phy-to-port link; the port is then the lower bound."""
    controller = _controller(host_phys=8, wide_members=0, drives=20)

    assert (controller.ports_used, controller.ports_free) == (1, 7)


def test_a_two_phy_wide_port_with_four_drives_uses_two_of_eight_phys() -> None:
    controller = _controller(host_phys=8, wide_members=2, drives=4)

    assert (controller.port_count, controller.ports_used, controller.ports_free) == (8, 2, 6)


def test_expander_phys_linked_to_the_hba_port_are_not_phys_the_controller_uses() -> None:
    """The kernel links an expander's phys to the port above it, but they are the expander's, not the HBA's."""
    controller = _controller(host_phys=8, wide_members=2, drives=4, expander_phys=2)

    assert (controller.port_count, controller.ports_used, controller.ports_free) == (8, 2, 6)
