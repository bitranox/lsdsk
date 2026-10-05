"""A captured link speed or width is a reading only where the specification allows it.

Linux and Windows both hand the builders a link as text, and the builders are
the layer whose tolerant job it is to decode that text. Three defects lived
there, each measured on a crafted capture:

* ``[0-9.]+`` matched ``.``, ``1.2.3`` and ``6..0``, and ``float`` then raised,
  so every command died at exit 22 with a bare ``ValueError`` and no JSON
  envelope; 400 nines parsed to ``inf``.
* A width was any integer: ``-8`` and ``1000000`` were printed as readings
  (``Gen3x-8``, ``Gen4x1000000``), and a 4000-digit one overflowed at exit 70.
* Every distinct raw shape became its own placement group, so a capture of
  ports with distinct widths cost the product of its cards and its ports.

Each is answered at the parse, as "not measured", which is what the parse
functions' docstrings already promised.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from workcount import work_to_run

from lsdsk.adapters.hw.linux.builder import (
    parse_link_rate,
    parse_pcie_running_width,
    parse_pcie_speed,
    parse_pcie_width,
)
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

#: Text that is not a number the regex should have accepted, or a number no
#: PCIe lane runs at.
MALFORMED_SPEEDS = (
    ".",
    ". GT/s",
    "1.2.3 GT/s PCIe",
    "6..0 GT/s PCIe",
    "9" * 400 + " GT/s PCIe",
    "7.0 GT/s PCIe",
    "0 GT/s PCIe",
    "8.000001 GT/s PCIe",
)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", MALFORMED_SPEEDS)
def test_a_speed_that_is_not_a_pcie_lane_rate_is_not_measured(text: str) -> None:
    """Each malformed or off-table speed reads as absent rather than raising or inventing a rate."""
    assert parse_pcie_speed(text) is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.5 GT/s PCIe", 2.5),
        ("5.0 GT/s PCIe", 5.0),
        ("5 GT/s", 5.0),
        ("8.0 GT/s PCIe", 8.0),
        ("16.0 GT/s PCIe", 16.0),
        ("32.0 GT/s PCIe", 32.0),
        ("64.0 GT/s PCIe", 64.0),
    ],
)
def test_every_pcie_lane_rate_still_parses(text: str, expected: float) -> None:
    """The control for the arm above: every rate the specification defines, older kernels' spelling included."""
    assert parse_pcie_speed(text) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", [".", ". Gbit", "1.2.3 Gbit", "6..0 Gbit", "9" * 400 + " Gbit"])
def test_a_malformed_sas_or_sata_rate_is_not_measured(text: str) -> None:
    """The SAS and SATA rate shares the pattern, so it shares the refusal; ``inf`` is not a rate."""
    assert parse_link_rate(text) is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", ["-8", "0", "33", "255", "1000000", "9" * 4000, "x8", ""])
def test_a_capable_width_outside_the_specification_is_not_measured(text: str) -> None:
    """Max Link Width is 1 to 32 lanes; anything else is not a width a port can offer."""
    assert parse_pcie_width(text) is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", ["-8", "33", "1000000", "9" * 4000])
def test_a_running_width_outside_the_specification_is_not_measured(text: str) -> None:
    """A negotiated width past 32 lanes, or below zero, is not a link that trained."""
    assert parse_pcie_running_width(text) is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("width", [1, 2, 4, 8, 12, 16, 32])
def test_every_specified_width_still_parses(width: int) -> None:
    """The control for both width arms, at each width the specification names."""
    assert parse_pcie_width(str(width)) == width
    assert parse_pcie_running_width(str(width)) == width


@pytest.mark.os_agnostic
def test_a_running_width_of_zero_stays_a_reading() -> None:
    """Zero lanes negotiated is a link that never trained, which a rule calls critical.

    Bounding the running width at 1 would turn that finding into silence, so
    zero is kept here and refused only for the capable width, where it is not
    a width any port offers.
    """
    assert parse_pcie_running_width("0") == 0


def _capture_with_link(**link: str) -> dict[str, Any]:
    """A Linux capture holding one NVMe controller whose link fields are ``link``."""
    entry: dict[str, Any] = {"class": "0x010802", "path": "/sys/devices/pci0000:00/0000:00:01.0", **link}
    return {"schema": 2, "platform": "linux", "hostname": "h", "kernel": "k", "pci": {"0000:00:01.0": entry}}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "link",
    [
        {"max_link_speed": ". GT/s"},
        {"current_link_speed": "6..0 GT/s PCIe"},
        {"max_link_speed": "9" * 400 + " GT/s PCIe", "max_link_width": "4"},
        {"max_link_speed": "8.0 GT/s PCIe", "max_link_width": "9" * 4000},
        {"max_link_speed": "8.0 GT/s PCIe", "current_link_width": "9" * 4000},
    ],
    ids=["dot", "double-point", "infinite", "4000-digit-width", "4000-digit-running-width"],
)
def test_a_capture_with_a_malformed_link_still_reports_its_findings(
    link: dict[str, str],
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """End to end: the replay exits with its findings' code and a JSON envelope, not 22 or 70."""
    from lsdsk.adapters.cli import cli

    crafted = tmp_path / "malformed-link.json"
    crafted.write_text(json.dumps(_capture_with_link(**link)), encoding="utf-8")

    result = cli_runner.invoke(
        cli,
        ["--replay", str(crafted), "--no-record", "findings", "--format", "json"],
        obj=production_factory,
        color=False,
    )

    assert result.exit_code in {0, 1, 2}, f"exit {result.exit_code}: {result.stderr[-400:]}"
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "findings", envelope


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("width", "printed"),
    [("-8", None), ("1000000", None), ("8", "Gen3x8")],
    ids=["negative", "million-lanes", "control-x8"],
)
def test_a_width_outside_the_specification_is_not_printed_as_a_reading(
    width: str,
    printed: str | None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """The controller table drew ``Gen3x-8`` and ``Gen3x1000000`` as the running and capable links.

    The control arm holds that a real width still reaches the same column, so
    the absence in the other two is the parse and not a table that prints no
    width at all.
    """
    from lsdsk.adapters.cli import cli

    link = {
        "max_link_speed": "8.0 GT/s PCIe",
        "current_link_speed": "8.0 GT/s PCIe",
        "max_link_width": width,
        "current_link_width": width,
    }
    crafted = tmp_path / "width.json"
    crafted.write_text(json.dumps(_capture_with_link(**link)), encoding="utf-8")

    result = cli_runner.invoke(
        cli, ["--replay", str(crafted), "--no-record", "controllers"], obj=production_factory, color=False
    )

    assert result.exit_code == 0, result.stderr[-400:]
    if printed is None:
        assert f"Gen3x{width}" not in result.stdout, f"Gen3x{width} was printed as a reading"
    else:
        assert printed in result.stdout, f"the control width is missing from the table:\n{result.stdout}"


#: How much the doubled machine may cost the single one. Searching every
#: distinct port shape per capped card made the doubling about four times the
#: work at these sizes; one search per bounded shape doubles it.
_ACCEPTABLE_GROWTH = 2.5


def _capped_cards_beside_free_ports(count: int) -> dict[str, Any]:
    """``count`` capped graphics cards, and ``count`` free ports each claiming a different width.

    Every card is a Gen3 x16 card in a Gen3 x8 root port, so each one asks the
    free-slot search. Each free port claims a width no port can have
    (``33 + i``), which is distinct per port as a crafted capture can make it.
    """
    pci: dict[str, Any] = {}
    for index in range(count):
        domain = f"{index + 1:04x}"
        port, card, free = f"{domain}:00:01.0", f"{domain}:01:00.0", f"{domain}:00:02.0"
        pci[port] = {
            "class": "0x060400",
            "path": f"/sys/devices/pci{domain}:00/{port}",
            "pcie_port_type": 4,
            "slot_implemented": True,
            "max_link_speed": "8.0 GT/s PCIe",
            "max_link_width": "8",
            "current_link_speed": "8.0 GT/s PCIe",
            "current_link_width": "8",
            "children": [card],
        }
        pci[card] = {
            "class": "0x030000",
            "path": f"/sys/devices/pci{domain}:00/{port}/{card}",
            "max_link_speed": "8.0 GT/s PCIe",
            "max_link_width": "16",
            "current_link_speed": "8.0 GT/s PCIe",
            "current_link_width": "8",
        }
        pci[free] = {
            "class": "0x060400",
            "path": f"/sys/devices/pci{domain}:00/{free}",
            "pcie_port_type": 4,
            "slot_implemented": True,
            "max_link_speed": "8.0 GT/s PCIe",
            "max_link_width": str(33 + index),
            "current_link_speed": "8.0 GT/s PCIe",
            "current_link_width": "0",
        }
    return {"schema": 2, "platform": "linux", "hostname": "h", "kernel": "k", "euid": 0, "pci": pci}


def _work_to_grade(count: int) -> int:
    capture = _capped_cards_beside_free_ports(count)

    def grade() -> int:
        findings = diagnose(build_from(capture))
        return sum("is capped by its slot" in finding.title for finding in findings)

    capped, work = work_to_run(grade)
    # The control: every card was graded and reached the search, so a machine
    # the rules never searched cannot pass for one they searched cheaply.
    assert capped == count, f"{capped} of {count} cards were graded as capped"
    return work


@pytest.mark.os_agnostic
def test_ports_claiming_distinct_widths_do_not_multiply_the_placement_search() -> None:
    """Twice the cards beside twice the distinct port shapes costs about twice the work."""
    single = _work_to_grade(100)
    doubled = _work_to_grade(200)
    assert doubled / single <= _ACCEPTABLE_GROWTH, (
        f"doubling 100 cards took {doubled} steps against {single}, {doubled / single:.2f} times the work: "
        "every distinct raw port shape is being searched per card"
    )
