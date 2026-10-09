"""The SMART page names why a disk shows no attribute table, and never blames privilege that was held."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.render.report import render_smart
from lsdsk.domain.enums import BusType
from lsdsk.domain.models import Disk, Inventory, RefusedReading

if TYPE_CHECKING:
    from collections.abc import Callable

_NEEDS_ROOT = "needs root or Administrator"


def _unread_disk(*, refused: tuple[RefusedReading, ...] = ()) -> Disk:
    return Disk(node="sda", path="/dev/sda", model="ACME", bus=BusType.SATA, readings_refused=refused)


@pytest.mark.os_agnostic
def test_an_unprivileged_run_is_told_to_elevate(rendered: Callable[..., str]) -> None:
    inventory = Inventory(hostname="box", privileged=False, disks=(_unread_disk(),))
    assert _NEEDS_ROOT in rendered(render_smart(inventory), width=140)


@pytest.mark.os_agnostic
def test_a_privileged_run_that_was_refused_names_the_refusal_not_privilege(rendered: Callable[..., str]) -> None:
    refusal = RefusedReading(reading="smart-data", reason="passthrough refused, Win32 error 5")
    inventory = Inventory(hostname="box", privileged=True, disks=(_unread_disk(refused=(refusal,)),))
    output = rendered(render_smart(inventory), width=200)
    assert _NEEDS_ROOT not in output
    assert "smart-data" in output
    assert "Win32 error 5" in output


@pytest.mark.os_agnostic
def test_a_privileged_run_with_no_recorded_refusal_does_not_blame_privilege(rendered: Callable[..., str]) -> None:
    inventory = Inventory(hostname="box", privileged=True, disks=(_unread_disk(),))
    output = rendered(render_smart(inventory), width=140)
    assert _NEEDS_ROOT not in output
    assert "no SMART data was read" in output
