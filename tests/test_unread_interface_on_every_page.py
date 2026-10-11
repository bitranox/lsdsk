"""Every page that lists disks says when a disk interface could not be read.

A Windows disk interface whose device path could not be read is not counted as a
disk, so a page that lists disks and is silent about it reads as a machine with
one drive fewer. The sentence lived in the header only, which the topology,
findings and report pages draw; the other sections exited 0 saying nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.cli import cli

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"
_SENTENCE = "1 disk interface not read: could not read the device interface path, Win32 error 5."

PAGES = ("topology", "controllers", "disks", "health", "smart", "slots", "trend", "findings", "report")


def _replay_with_an_unreadable_interface(tmp_path: Path) -> Path:
    """The windows-ahci capture with its drives replaced by one interface nobody could read."""
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["disks"] = {
        "unreadable disk interface 1": {
            "path": "unreadable disk interface 1",
            "parent": None,
            "ancestors": [],
            "error": "could not read the device interface path, Win32 error 5",
            "interface_unreadable": True,
        }
    }
    replay = tmp_path / "unreadable.json"
    replay.write_text(json.dumps(payload), encoding="utf-8")
    return replay


def _page(runner: CliRunner, factory: Callable[[], Any], page: str, replay: Path) -> str:
    """The human page `page` prints for `replay`, on standard output alone."""
    result = runner.invoke(cli, [page, "--replay", str(replay)], obj=factory)
    assert result.stdout.strip(), f"{page} printed nothing, so it proved nothing: {result.output!r}"
    return " ".join(result.stdout.split())


@pytest.mark.parametrize("page", PAGES)
def test_a_page_that_lists_disks_says_an_interface_was_not_read(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, page: str
) -> None:
    output = _page(cli_runner, production_factory, page, _replay_with_an_unreadable_interface(tmp_path))

    assert _SENTENCE in output, output


@pytest.mark.parametrize("page", PAGES)
def test_a_page_is_silent_about_interfaces_when_every_one_was_read(
    cli_runner: CliRunner, production_factory: Callable[[], Any], page: str
) -> None:
    """The control: the untouched capture has no unread interface, so no page mentions one."""
    output = _page(cli_runner, production_factory, page, _FIXTURE)

    assert "interface not read" not in output
    assert "interfaces not read" not in output
