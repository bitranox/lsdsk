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

from hostprobe import (
    DOCUMENTED_EXITS,
    check,
    judge_filesystems,
    parse_zpool_list,
    resolve,
    root_fstype_from_mounts,
    smart_actually_read,
    usage_boot_disk_found,
    usb_link_read,
)


def _disk(bus: str, *, model: str | None = "ACME", hours: int | None = 100) -> dict[str, Any]:
    return {"bus": bus, "model": model, "health": None if hours is None else {"power_on_hours": hours}}


def _speed(lane_rate: str, lanes: int = 1) -> dict[str, Any]:
    return {"lane_rate": lane_rate, "lanes": lanes}


def _usb_disk(path: str, usb: dict[str, Any] | None) -> dict[str, Any]:
    return {**_disk("usb"), "path": path, "usb": usb}


_A_READ_LINK = {"running": _speed("480M"), "device_max": _speed("10G"), "port_max": _speed("480M")}


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


def _usb_check(results: list[dict[str, Any]]) -> dict[str, Any]:
    return next(item for item in results if item["name"] == "usb:every-usb-disk-has-a-running-link")


@pytest.mark.os_agnostic
def test_a_usb_disk_whose_link_was_read_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: a real USB disk with its link read, beside a disk on another bus."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_usb_disk("/dev/sdb", _A_READ_LINK), _disk("sata")]))
    results, facts = usb_link_read(["lsdsk"])
    assert _usb_check(results)["passed"], _usb_check(results)["detail"]
    assert facts["usb_disks"] == 1
    assert facts["usb_links"] == {"/dev/sdb": {"running": "480Mx1", "device_max": "10Gx1", "port_max": "480Mx1"}}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "usb",
    [None, {**_A_READ_LINK, "running": None}],
    ids=["no-usb-object", "no-running-link"],
)
def test_a_usb_disk_without_a_running_link_fails_naming_it(
    monkeypatch: pytest.MonkeyPatch, usb: dict[str, Any] | None
) -> None:
    """A USB disk the reader could not place on its link is the failure this check exists for."""
    import hostprobe

    disks = [_usb_disk("/dev/sdb", _A_READ_LINK), _usb_disk("/dev/sdc", usb)]
    monkeypatch.setattr(hostprobe, "run", _fake_cli(disks))
    results, _ = usb_link_read(["lsdsk"])
    found = _usb_check(results)
    assert not found["passed"], "a USB disk with no running link was called fine"
    assert "/dev/sdc" in found["detail"]
    assert "/dev/sdb" not in found["detail"], found["detail"]


@pytest.mark.os_agnostic
def test_a_host_with_no_usb_disk_passes_and_says_it_proved_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vacuous pass must SAY it is vacuous, or the report reads as USB coverage."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _fake_cli([_disk("sata"), _disk("nvme")]))
    results, facts = usb_link_read(["lsdsk"])
    found = _usb_check(results)
    assert found["passed"]
    assert found["detail"] == "no usb disk on this host"
    assert facts["usb_disks"] == 0


_NO_USAGE = object()


def _used_disk(path: str, usage: object) -> dict[str, Any]:
    """A disk as ``disks --format json`` writes it, with ``usage`` left out for ``_NO_USAGE``."""
    disk: dict[str, Any] = {**_disk("sata"), "path": path}
    if usage is not _NO_USAGE:
        disk["usage"] = usage
    return disk


def _usage_cli(disks: list[dict[str, Any]], environment: str):
    """The CLI's ``disks`` envelope, carrying the environment the check keys on."""
    import json

    payload = json.dumps({"ok": True, "command": "disks", "data": {"disks": disks, "environment": environment}})

    def run(_invocation: list[str], _args: list[str]) -> tuple[int, str, str]:
        return 0, payload, ""

    return run


_BOOT: dict[str, Any] = {"boot": True, "uses": [{"kind": "mount", "name": "", "mounts": ["/"]}]}
_DATA: dict[str, Any] = {"boot": False, "uses": []}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("disks", "environment"),
    [
        pytest.param([_used_disk("/dev/sda", _BOOT), _used_disk("/dev/sdb", _DATA)], "bare-metal", id="boot-found"),
        pytest.param([_used_disk("/dev/sda", None), _used_disk("/dev/sdb", None)], "container", id="container-unread"),
    ],
)
def test_the_boot_disk_check_passes_a_found_boot_disk_and_an_unread_container(
    monkeypatch: pytest.MonkeyPatch, disks: list[dict[str, Any]], environment: str
) -> None:
    """The controls: a host whose boot disk was found, and a container that can see no host use."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _usage_cli(disks, environment))
    results, _ = usage_boot_disk_found(["lsdsk"])
    assert results[0]["passed"], results[0]["detail"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("disks", "environment"),
    [
        pytest.param([_used_disk("/dev/sda", None)], "bare-metal", id="bare-metal-all-unread"),
        pytest.param([_used_disk("/dev/sda", None)], "vm", id="vm-all-unread"),
        pytest.param([], "container", id="no-disks"),
        pytest.param([_used_disk("/dev/sda", _BOOT), _used_disk("/dev/sdb", _NO_USAGE)], "bare-metal", id="no-key"),
        pytest.param([_used_disk("/dev/sda", _NO_USAGE)], "container", id="container-no-key"),
    ],
)
def test_the_boot_disk_check_fails_a_broken_reading(
    monkeypatch: pytest.MonkeyPatch, disks: list[dict[str, Any]], environment: str
) -> None:
    """All unread is accepted only in a container; no disks or a missing ``usage`` key always fail."""
    import hostprobe

    monkeypatch.setattr(hostprobe, "run", _usage_cli(disks, environment))
    results, _ = usage_boot_disk_found(["lsdsk"])
    assert not results[0]["passed"], results[0]["detail"]


_GIB = 1024**3


def _pool(name: str, *, used: int | None, free: int | None, kind: str = "pool") -> dict[str, Any]:
    """A filesystem row as ``filesystems --format json`` writes it."""
    return {
        "kind": kind,
        "name": name,
        "size_bytes": None if used is None or free is None else used + free,
        "used_bytes": used,
        "free_bytes": free,
    }


def _root(*, used: int | None = 10 * _GIB, free: int | None = 90 * _GIB) -> dict[str, Any]:
    return _pool("/", used=used, free=free, kind="mount")


def _judged(
    rows: list[dict[str, Any]] | None,
    zpool: str | None,
    *,
    environment: str = "bare-metal",
    system: str = "Linux",
    root_fstype: str | None = "ext4",
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """The checks by name, and the facts, for one host's reading."""
    data: dict[str, Any] = {"filesystems": rows, "environment": environment}
    results, facts = judge_filesystems(data, zpool, system=system, root_fstype=root_fstype)
    return {item["name"]: item for item in results}, facts


#: ``zpool list -Hp -o name,size,alloc,free,cap`` for a pool 50 percent full, tab separated.
_ZPOOL_HALF = f"tank\t{100 * _GIB}\t{50 * _GIB}\t{50 * _GIB}\t50\n"


def test_zpool_list_output_parses_into_exact_bytes_and_the_cap() -> None:
    parsed = parse_zpool_list(f"rpool\t{100 * _GIB}\t{25 * _GIB}\t{75 * _GIB}\t25\ntank\t10\t5\t5\t50\n")
    assert parsed["rpool"].alloc == 25 * _GIB
    assert parsed["rpool"].cap == 25
    assert set(parsed) == {"rpool", "tank"}
    assert parse_zpool_list("") == {}


def test_a_zpool_line_that_is_not_five_numbers_is_refused_not_guessed() -> None:
    with pytest.raises(ValueError, match="zpool list line"):
        parse_zpool_list("tank\t100\t50\n")


def test_a_pool_row_equal_to_the_cap_agrees() -> None:
    rows = [_root(), _pool("tank", used=50 * _GIB + 1, free=50 * _GIB)]  # floor gives 50, cap says 50
    checks, facts = _judged(rows, _ZPOOL_HALF)
    assert checks["filesystems:pools-agree-with-zpool"]["passed"], checks["filesystems:pools-agree-with-zpool"][
        "detail"
    ]
    assert facts["zfs_pools_checked"] == 1


def test_a_pool_row_one_point_above_the_cap_disagrees() -> None:
    rows = [_root(), _pool("tank", used=51 * _GIB, free=49 * _GIB)]  # 51, cap says 50
    found = _judged(rows, _ZPOOL_HALF)[0]["filesystems:pools-agree-with-zpool"]
    assert not found["passed"]
    assert "lsdsk 51%" in found["detail"]


def test_a_pool_row_two_points_from_the_cap_disagrees_and_names_both_figures() -> None:
    rows = [_root(), _pool("tank", used=52 * _GIB, free=48 * _GIB)]
    found = _judged(rows, _ZPOOL_HALF)[0]["filesystems:pools-agree-with-zpool"]
    assert not found["passed"]
    assert "tank" in found["detail"]
    assert "52" in found["detail"]
    assert "cap 50" in found["detail"]


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([_root()], id="pool-missing-from-lsdsk"),
        pytest.param([_root(), _pool("tank", used=None, free=None)], id="pool-row-unread"),
        pytest.param(
            [_root(), _pool("tank", used=50 * _GIB, free=50 * _GIB), _pool("ghost", used=1, free=1)],
            id="row-zpool-lacks",
        ),
    ],
)
def test_a_pool_the_two_sides_do_not_both_report_disagrees(rows: list[dict[str, Any]]) -> None:
    found = _judged(rows, _ZPOOL_HALF)[0]["filesystems:pools-agree-with-zpool"]
    assert not found["passed"], found["detail"]


@pytest.mark.parametrize("zpool", [None, ""], ids=["no-zpool-binary", "no-pools"])
def test_a_host_without_zfs_makes_no_pool_claim_and_counts_zero_pools(zpool: str | None) -> None:
    checks, facts = _judged([_root()], zpool)
    assert "filesystems:pools-agree-with-zpool" not in checks
    assert facts["zfs_pools_checked"] == 0


def test_a_filesystem_row_for_a_pool_on_a_host_without_zfs_still_fails() -> None:
    found = _judged([_root(), _pool("tank", used=1, free=1)], None)[0]["filesystems:pools-agree-with-zpool"]
    assert not found["passed"], "lsdsk invented a pool zpool cannot see"


def test_the_root_row_with_every_figure_read_passes() -> None:
    found = _judged([_root()], None)[0]["filesystems:root-row-populated"]
    assert found["passed"], found["detail"]


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([_pool("/data", used=1, free=1, kind="mount")], id="no-root-row"),
        pytest.param([_root(used=None)], id="used-unread"),
        pytest.param([_root(free=None)], id="free-unread"),
        pytest.param(None, id="filesystems-not-read"),
    ],
)
def test_a_bare_metal_host_must_show_a_populated_root_row(rows: list[dict[str, Any]] | None) -> None:
    found = _judged(rows, None)[0]["filesystems:root-row-populated"]
    assert not found["passed"], found["detail"]


def test_a_container_may_show_no_root_row_and_the_exemption_names_its_reason() -> None:
    checks, facts = _judged(None, None, environment="container")
    assert checks["filesystems:root-row-populated"]["passed"]
    assert "container" in facts["root_row"]


def test_a_container_that_does_show_the_root_row_is_judged_on_it() -> None:
    found = _judged([_root(used=None)], None, environment="container")[0]["filesystems:root-row-populated"]
    assert not found["passed"], "an exemption for an absent row must not excuse an unread one"


def test_windows_has_no_root_row_and_says_so() -> None:
    checks, facts = _judged([], None, system="Windows")
    assert "filesystems:root-row-populated" not in checks
    assert "not applicable" in facts["root_row"]


def test_a_filesystems_reading_that_did_not_parse_fails_loudly() -> None:
    results, _ = judge_filesystems({"environment": "bare-metal"}, None, system="Linux", root_fstype="ext4")
    assert any(not item["passed"] for item in results)


def test_the_root_fstype_is_the_last_mount_over_slash() -> None:
    mounts = "rpool/ROOT/pve-1 / zfs rw 0 0\nproc /proc proc rw 0 0\n/dev/sda1 / ext4 rw 0 0\n"
    assert root_fstype_from_mounts(mounts) == "ext4"
    assert root_fstype_from_mounts("proc /proc proc rw 0 0\n") is None


def test_a_zfs_root_is_covered_by_its_pool_row_and_the_exemption_names_that() -> None:
    rows = [_pool("rpool", used=50 * _GIB, free=50 * _GIB)]
    checks, facts = _judged(rows, _ZPOOL_HALF.replace("tank", "rpool"), root_fstype="zfs")
    assert checks["filesystems:root-row-populated"]["passed"], checks["filesystems:root-row-populated"]["detail"]
    assert "zfs" in facts["root_row"]


def test_a_zfs_root_with_no_populated_pool_row_still_fails() -> None:
    rows = [_pool("rpool", used=None, free=None)]
    found = _judged(rows, None, root_fstype="zfs")[0]["filesystems:root-row-populated"]
    assert not found["passed"], "a ZFS root was excused although no pool row carries its figures"
    assert not _judged([], None, root_fstype="zfs")[0]["filesystems:root-row-populated"]["passed"]


def test_a_non_zfs_root_is_not_excused_by_an_unrelated_pool_row() -> None:
    rows = [_pool("tank", used=1, free=1)]
    found = _judged(rows, _ZPOOL_HALF, root_fstype="ext4")[0]["filesystems:root-row-populated"]
    assert not found["passed"]
