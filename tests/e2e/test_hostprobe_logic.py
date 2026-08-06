"""The probe's own judgement, tested without a host.

A hardware probe that cannot fail is worse than none: it reports green from six
machines and means nothing. The first version of the counter check asked only
whether SOME drive had power-on hours, which passed on a build where every NVMe
drive had silently lost them, because the SATA drive still answered. These pin
the judgement so that cannot come back.

Runs everywhere and needs no fleet, unlike ``test_real_hardware``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from hostprobe import DOCUMENTED_EXITS, check, resolve, smart_actually_read


def _disk(bus: str, *, model: str | None = "ACME", hours: int | None = 100) -> dict[str, Any]:
    return {"bus": bus, "model": model, "health": None if hours is None else {"power_on_hours": hours}}


def _fake_cli(disks: list[dict[str, Any]], *, privileged: bool = True):
    """Stand in for the CLI at the process boundary, which is a real edge.

    The probe's only input is another program's stdout, so substituting that is
    substituting an external process, not an internal collaborator.
    """
    import json

    payload = json.dumps({"ok": True, "command": "disks", "data": {"disks": disks, "privileged": privileged}})

    def run(_invocation: list[str], _args: list[str]) -> tuple[int, str, str]:
        return 0, payload, ""

    return run


@pytest.mark.os_agnostic
def test_a_bus_whose_drives_all_lost_their_counters_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The measured Windows failure: NVMe blind, SATA fine, machine still 'has counters'."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_disk("nvme", hours=None), _disk("sata")]))
    results, facts = smart_actually_read(["lsdsk"])
    bus_check = next(item for item in results if item["name"] == "smart:every-bus-reads-counters")
    assert not bus_check["passed"], "a whole transport read nothing and the probe called it fine"
    assert facts["blind_buses"] == ["nvme"]


@pytest.mark.os_agnostic
def test_every_bus_answering_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control. A check that fails on everything is no better than one that fails on nothing."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_disk("nvme"), _disk("sata")]))
    results, facts = smart_actually_read(["lsdsk"])
    bus_check = next(item for item in results if item["name"] == "smart:every-bus-reads-counters")
    assert bus_check["passed"], f"a healthy machine was failed: {bus_check['detail']}"
    assert facts["blind_buses"] == []


@pytest.mark.os_agnostic
def test_a_virtual_disk_without_counters_is_not_a_blind_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hypervisor's virtual disk publishes no SMART, and never did.

    Counting it would fail every VM and every Windows host with a mounted image,
    which trains people to ignore the result.
    """
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_disk("virtual", hours=None), _disk("nvme")]))
    results, _ = smart_actually_read(["lsdsk"])
    bus_check = next(item for item in results if item["name"] == "smart:every-bus-reads-counters")
    assert bus_check["passed"], "a virtual disk with no SMART was treated as a failed transport"


@pytest.mark.os_agnostic
def test_an_unidentified_drive_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty model means the identify came back with nothing."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_disk("nvme", model=None), _disk("sata")]))
    results, _ = smart_actually_read(["lsdsk"])
    identified = next(item for item in results if item["name"] == "smart:every-disk-identified")
    assert not identified["passed"]


@pytest.mark.os_agnostic
def test_an_unprivileged_run_is_not_asked_for_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    """It cannot read them, so requiring them would fail every unelevated host."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_disk("nvme", hours=None)], privileged=False))
    results, _ = smart_actually_read(["lsdsk"])
    assert not [item for item in results if item["name"] == "smart:every-bus-reads-counters"]


@pytest.mark.os_agnostic
def test_resolve_turns_a_bare_name_into_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows failure mode: a bare name resolving somewhere unintended.

    The PATH lookup is stubbed rather than trusted, because asking whether some
    real program happens to be installed makes the result depend on the machine
    the suite runs on, which is the same class of problem the probe exists to
    remove.
    """
    import hostprobe

    def which(name: str, *_args: object, **_kwargs: object) -> str | None:
        return "/somewhere/bin/lsdsk" if name == "lsdsk" else None

    monkeypatch.setattr(hostprobe.shutil, "which", which)
    assert resolve(["lsdsk", "disks"]) == ["/somewhere/bin/lsdsk", "disks"], "a resolvable name was left bare"
    assert resolve(["not-a-program"]) == ["not-a-program"], "an unresolvable name must be passed through unchanged"
    assert resolve([]) == []


@pytest.mark.os_agnostic
def test_the_documented_exit_codes_match_the_tool() -> None:
    """If the tool's enum grows a code, this list has to know."""
    from lsdsk.adapters.cli.exit_codes import ExitCode

    raised = {int(code) for code in ExitCode} - {130, 141, 143}
    assert raised <= DOCUMENTED_EXITS | {0}, (
        f"the probe would reject a code the tool raises: {raised - DOCUMENTED_EXITS}"
    )


@pytest.mark.os_agnostic
def test_check_reports_both_outcomes() -> None:
    """A helper that only ever says 'passed' would make every check above green."""
    assert check("x", True)["passed"] is True
    assert check("x", False, "why")["passed"] is False
    assert check("x", False, "why")["detail"] == "why"
