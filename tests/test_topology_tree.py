"""What the topology tree says about a link, including when it says nothing.

The tree's own law is the one the whole tool is built on: an unread end is never
a capable end. A reading that was not taken has to LOOK different from one that
was taken and was fine, because a reader who cannot tell them apart will assume
the second. A blank is the worst possible rendering of "not measured", since it
is also how a page renders a value nobody thought worth mentioning.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report
from lsdsk.domain.diagnostics import diagnose

if TYPE_CHECKING:
    from rich.console import RenderableType

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# A real AHCI controller whose capture carries no link keys at all, which is
# what the chipset publishes on this board.
_UNREAD_CONTROLLER = "0000:00:1f.2"


def _load(host: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return payload


def _rendered(renderable: RenderableType, width: int = 200) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _line_for(address: str, host: str = "linux-sas-hba") -> str:
    machine = build_from(_load(host))
    text = _rendered(report.render_tree(machine, diagnose(machine)))
    return next(line for line in text.splitlines() if address in line)


@pytest.mark.os_agnostic
def test_a_storage_controller_that_published_no_link_says_so() -> None:
    """Verify a controller whose link was never read is not rendered as though it were fine.

    This controller's capture carries no link keys whatever, so the tree printed
    its address, name and driver and then stopped, saying nothing about its
    link. Beside a controller reading ``PCIe 3.0 x8`` that reads as a device
    with nothing to report rather than one nothing could be read from.
    """
    machine = build_from(_load("linux-sas-hba"))
    controller = next(item for item in machine.controllers if item.address == _UNREAD_CONTROLLER)
    assert not controller.link.capability_is_known, "the fixture no longer has a controller with an unread link"
    assert controller.link.current_speed_gtps is None

    line = _line_for(_UNREAD_CONTROLLER)

    assert "PCIe" in line, f"the line says nothing about its link: {line!r}"
    assert "not read" in line, f"an unread link must say so rather than go blank: {line!r}"


@pytest.mark.os_agnostic
def test_a_controller_whose_link_was_read_is_unchanged() -> None:
    """Verify the control: a measured link still reads as it did, with no caveat."""
    line = _line_for("0000:03:00.0")

    assert "PCIe 3.0 x8" in line
    assert "not read" not in line, f"a measured link must carry no caveat: {line!r}"
