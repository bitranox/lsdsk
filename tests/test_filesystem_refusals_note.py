"""A refused filesystem reading is named on the filesystems views, from the recorded refusal.

The inventories are built through the real platform builders from a committed
capture plus the one field a run would have left, so the sentence is judged on
what the builder records and not on a hand-made `unread_filesystems`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console, RenderableType
from textual.widgets import Static

from lsdsk.adapters.config.tunables import DisplaySettings, Tunables
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import full, tables
from lsdsk.adapters.tui import LsdskApp
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
UNELEVATED_SENTENCE = "Not read: fragmentation (needs Administrator)."


def _payload(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _windows_machine(*, bitmaps_read: bool) -> Inventory:
    """windows-ahci with one NTFS volume C: on its disk, as an elevated or unelevated run left it."""
    payload = _payload("windows-ahci")
    payload["volumes"] = {
        "0": {
            "paths": ["C:\\"],
            "disks": [0],
            "fs_type": "NTFS",
            "total_bytes": 100,
            "free_bytes": 40,
            "available_bytes": 30,
        }
    }
    payload["volume_bitmaps_read"] = bitmaps_read
    return build_from(payload)


def _linux_machine(*, pools_error: str | None) -> Inventory:
    payload = _payload("linux-zfs-filesystems")
    if pools_error is not None:
        payload["zfs_pools_error"] = pools_error
    return build_from(payload)


def _drawn(renderable: RenderableType) -> str:
    sink = io.StringIO()
    Console(width=200, file=sink, force_terminal=False).print(renderable)
    return sink.getvalue()


def _printed(inventory: Inventory) -> str:
    return _drawn(tables.render_filesystems(inventory, [], width=200))


def test_the_unelevated_windows_run_records_the_refusal_the_note_keys_on() -> None:
    assert [r.reading for r in _windows_machine(bitmaps_read=False).unread_filesystems] == ["fragmentation"]
    assert _windows_machine(bitmaps_read=True).unread_filesystems == ()


def test_the_printed_view_names_the_refusal_of_an_unelevated_windows_run() -> None:
    text = _printed(_windows_machine(bitmaps_read=False))
    assert UNELEVATED_SENTENCE in text
    assert "Administrator" not in _printed(_windows_machine(bitmaps_read=True))


def test_the_printed_view_names_a_linux_pool_refusal_and_its_reason() -> None:
    inventory = _linux_machine(pools_error="pool tank: stats refused")
    assert "Not read: zfs-pools (pool tank: stats refused)." in _printed(inventory)
    assert "Not read" not in _printed(_linux_machine(pools_error=None))


def test_the_default_page_carries_the_same_sentence() -> None:
    assert UNELEVATED_SENTENCE in _full_page(_windows_machine(bitmaps_read=False))
    assert UNELEVATED_SENTENCE not in _full_page(_windows_machine(bitmaps_read=True))


def _full_page(inventory: Inventory) -> str:
    tunables = Tunables(DEFAULT_THRESHOLDS, DisplaySettings())
    return _drawn(full.render_full(inventory, [], width=200, tunables=tunables))


async def _page_legend(inventory: Inventory) -> str:
    app = LsdskApp(inventory)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        legend = app.query_one("#filesystem-legend", Static)
        return str(legend.content) if legend.display else ""


@pytest.mark.asyncio
async def test_the_interactive_page_names_the_refusal_of_an_unelevated_windows_run() -> None:
    assert UNELEVATED_SENTENCE in await _page_legend(_windows_machine(bitmaps_read=False))


@pytest.mark.asyncio
async def test_the_interactive_page_names_a_linux_pool_refusal() -> None:
    legend = await _page_legend(_linux_machine(pools_error="pool tank: stats refused"))
    assert "Not read: zfs-pools (pool tank: stats refused)." in legend


@pytest.mark.asyncio
async def test_the_interactive_page_draws_no_sentence_when_nothing_was_refused() -> None:
    assert "Not read:" not in await _page_legend(_windows_machine(bitmaps_read=True))
    assert "Not read:" not in await _page_legend(_linux_machine(pools_error=None))
