"""Tests for what kind of machine the readings came from.

It changes what the output means, so getting it wrong is worse than not having
it: a container shows the host's hardware in full detail, and a guest shows
hardware the hypervisor invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.hw.decode.virtualization import (
    VirtualizationEvidence,
    classify,
    evidence_from_capture,
)
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.report import HEALTH_NEEDING_SMART, environment_caveat, privilege_note
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import Environment
from lsdsk.domain.models import Inventory

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


def capture() -> dict[str, Any]:
    """Load a real capture to reclassify."""
    with FIXTURE.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("evidence", "expected", "detail"),
    [
        (VirtualizationEvidence(container_marker="lxc"), Environment.CONTAINER, "LXC"),
        (VirtualizationEvidence(container_files=("docker",)), Environment.CONTAINER, "Docker"),
        (VirtualizationEvidence(cgroup="0::/docker/abc123"), Environment.CONTAINER, "Docker"),
        (VirtualizationEvidence(mount_markers="lxcfs"), Environment.CONTAINER, "LXC"),
        (VirtualizationEvidence(dmi_vendor="QEMU"), Environment.VIRTUAL_MACHINE, "QEMU"),
        (VirtualizationEvidence(dmi_product="VMware Virtual Platform"), Environment.VIRTUAL_MACHINE, "VMware"),
        (VirtualizationEvidence(dmi_vendor="innotek GmbH"), Environment.VIRTUAL_MACHINE, "VirtualBox"),
        (VirtualizationEvidence(hypervisor_type="xen"), Environment.VIRTUAL_MACHINE, "Xen"),
        (VirtualizationEvidence(hypervisor_flag=True), Environment.VIRTUAL_MACHINE, ""),
        (VirtualizationEvidence(dmi_vendor="ASUSTeK COMPUTER INC."), Environment.BARE_METAL, ""),
        (VirtualizationEvidence(), Environment.UNKNOWN, ""),
    ],
)
def test_classification(evidence: VirtualizationEvidence, expected: Environment, detail: str) -> None:
    """Verify each signal reaches the right verdict."""
    assert classify(evidence) == (expected, detail)


@pytest.mark.os_agnostic
def test_a_container_on_a_virtual_host_reports_as_a_container() -> None:
    """Verify the container wins, because it is what limits what you can do.

    A container inside a guest sees both sets of markers. The container is the
    binding one: from inside it, nothing about the hardware can be changed.
    """
    both = VirtualizationEvidence(container_marker="lxc", dmi_vendor="QEMU", hypervisor_flag=True)

    assert classify(both)[0] is Environment.CONTAINER


@pytest.mark.os_agnostic
def test_a_container_showing_host_dmi_is_still_a_container() -> None:
    """Verify host DMI does not mask a container.

    A container shares the host's kernel, so it reads the host's real DMI. That
    once made this box classify as bare metal, which is the whole bug.
    """
    evidence = VirtualizationEvidence(
        mount_markers="lxcfs",
        dmi_vendor="Micro-Star International Co., Ltd.",
        dmi_product="MS-7D27",
    )

    assert classify(evidence) == (Environment.CONTAINER, "LXC")


@pytest.mark.os_agnostic
def test_evidence_survives_a_snapshot_round_trip() -> None:
    """Verify a capture carries the evidence so a replay reaches the same verdict."""
    raw = {"container_marker": "lxc", "dmi_vendor": "QEMU", "hypervisor_flag": True}

    assert classify(evidence_from_capture(raw))[0] is Environment.CONTAINER
    assert evidence_from_capture({}).container_marker == ""


@pytest.mark.os_agnostic
def test_when_running_in_a_container_the_caveat_names_the_host() -> None:
    """Verify the reader is told whose hardware this is."""
    inventory = Inventory("h", environment=Environment.CONTAINER, environment_detail="LXC")
    caveat = environment_caveat(inventory)

    assert "container (LXC)" in caveat
    assert "HOST" in caveat
    assert "act on it there" in caveat, "the caveat must say where to act, not merely that this is not it"
    assert not inventory.hardware_is_local


@pytest.mark.os_agnostic
def test_when_running_in_a_vm_the_caveat_says_the_disks_are_invented() -> None:
    """Verify a guest is told its link speeds are not physical."""
    inventory = Inventory("h", environment=Environment.VIRTUAL_MACHINE, environment_detail="QEMU")

    assert "hypervisor presents" in environment_caveat(inventory)
    assert not inventory.readings_are_physical


@pytest.mark.os_agnostic
def test_bare_metal_gets_no_caveat() -> None:
    """Verify the normal case stays uncluttered."""
    assert environment_caveat(Inventory("h", environment=Environment.BARE_METAL)) == ""


@pytest.mark.os_agnostic
def test_missing_device_nodes_are_not_blamed_on_privilege() -> None:
    """Verify the advice matches the actual cause.

    Being unprivileged and having no device nodes look identical in the output.
    Telling a container user to run as root sends them to do something that
    cannot work.
    """
    no_nodes = Inventory("h", privileged=False, devices_accessible=False)
    unprivileged = Inventory("h", privileged=False, devices_accessible=True)
    fine = Inventory("h", privileged=True, devices_accessible=True)

    assert "elevating would not change that" in privilege_note(no_nodes)
    assert "Run as root" not in privilege_note(no_nodes)
    assert "Run as root" in privilege_note(unprivileged)
    assert privilege_note(fine) == ""


@pytest.mark.os_agnostic
def test_the_unavailable_note_names_the_columns_it_means() -> None:
    """Verify the reader is told which values are missing, not just that some are.

    Without the names, a dash in the table is ambiguous: it could mean zero or
    it could mean unknown, and those call for different actions.
    """
    note = privilege_note(Inventory("h", privileged=False, devices_accessible=False))

    for name, _ in HEALTH_NEEDING_SMART:
        assert name in note, f"{name} is promised by the model but not named to the reader"


@pytest.mark.os_agnostic
def test_every_named_value_is_a_real_health_field() -> None:
    """Verify the prose cannot drift away from what the model actually carries."""
    from dataclasses import fields

    from lsdsk.domain.models import Health

    known = {field.name for field in fields(Health)}
    for name, attribute in HEALTH_NEEDING_SMART:
        assert attribute in known, f"the note names {name!r}, but Health has no {attribute!r}"


@pytest.mark.os_agnostic
def test_in_a_vm_physical_link_rules_are_suppressed() -> None:
    """Verify cabling advice is not given about an emulated controller."""
    payload = capture()
    payload["environment"] = {"dmi_vendor": "QEMU", "hypervisor_flag": True}
    guest = build_from(payload)

    findings = diagnose(guest)

    assert guest.environment is Environment.VIRTUAL_MACHINE
    assert findings, "health findings still apply, only the link rules are dropped"
    assert all("linked at" not in finding.title for finding in findings)
    assert all("capped by the mainboard" not in finding.title for finding in findings)


@pytest.mark.os_agnostic
def test_in_a_container_physical_findings_are_kept() -> None:
    """Verify a container still reports real host faults.

    The hardware is real and the fault is real; only the place to fix it is
    elsewhere. Suppressing these would hide a genuine problem from the one
    person looking at it.
    """
    payload = capture()
    payload["environment"] = {"container_marker": "lxc"}
    contained = build_from(payload)

    bare = diagnose(build_from(capture()))
    inside = diagnose(contained)

    assert contained.environment is Environment.CONTAINER
    assert contained.readings_are_physical
    assert len(inside) == len(bare), "a container must not lose findings the host would report"


@pytest.mark.os_agnostic
def test_the_report_shows_the_caveat(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """Verify the caveat reaches the rendered report, not just the model."""
    from lsdsk.adapters import cli as cli_mod

    payload = capture()
    payload["environment"] = {"container_marker": "lxc"}
    snapshot = tmp_path / "contained.json"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", str(snapshot)], obj=production_factory)

    assert "container (LXC)" in result.output
    assert "HOST" in result.output


@pytest.mark.os_agnostic
def test_a_container_host_is_not_mistaken_for_a_container() -> None:
    """Verify a machine that merely serves containers reads as bare metal.

    A Proxmox host mounts lxcfs to serve its guests, and a Docker host has the
    same runtime filesystems present. Matching on the filesystem name alone
    classified both as containers, which is the exact opposite of the truth and
    would have told a host admin their hardware was somebody else's.
    """
    from lsdsk.adapters.hw.decode.virtualization import container_markers_in_mounts

    host = "412 33 0:62 / /var/lib/lxcfs rw,nosuid shared:83 - fuse.lxcfs lxcfs rw,user_id=0"
    inside = "6226 6210 0:99 /proc/cpuinfo /proc/cpuinfo rw,nosuid shared:3842 - fuse.lxcfs lxcfs rw,user_id=0"

    assert container_markers_in_mounts(host) == ""
    assert container_markers_in_mounts(inside) == "lxc"
    assert classify(VirtualizationEvidence(mount_markers=container_markers_in_mounts(host)))[0] is Environment.UNKNOWN


@pytest.mark.os_agnostic
def test_a_malformed_mount_table_is_survived() -> None:
    """Verify junk in the mount table does not raise."""
    from lsdsk.adapters.hw.decode.virtualization import container_markers_in_mounts

    assert container_markers_in_mounts("nonsense\n\n12 13 - \nx - y") == ""


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("host", "expected"),
    [("linux-sas-hba", Environment.BARE_METAL), ("linux-minimal", Environment.BARE_METAL)],
)
def test_captured_machines_classify_correctly(host: str, expected: Environment) -> None:
    """Verify the real captures reach the right verdict."""
    with (Path(__file__).parent / "fixtures" / "hw" / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)

    assert build_from(payload).environment is expected


@pytest.mark.os_agnostic
def test_the_windows_capture_is_recognised_as_a_guest() -> None:
    """Verify the Windows machine, which really is a QEMU guest, says so."""
    with (Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    inventory = build_from(payload)

    assert inventory.environment is Environment.VIRTUAL_MACHINE
    assert inventory.environment_detail == "QEMU"


@pytest.mark.os_agnostic
def test_the_header_names_the_mainboard_when_dmi_carried_it(rendered: Callable[..., str]) -> None:
    """Verify the board reaches the banner.

    The placement hints reason about what "this board" offers, so the board it
    means has to be on screen; otherwise the advice cannot be acted on without
    going and looking the machine up.
    """
    from lsdsk.adapters.render.report import render_header
    from lsdsk.domain.models import Inventory

    named = Inventory("h", board="Micro-Star International Co., Ltd. MEG Z690 ACE (MS-7D27)")
    anonymous = Inventory("h")

    assert "MEG Z690 ACE (MS-7D27)" in rendered(render_header(named))
    assert "MEG Z690" not in rendered(render_header(anonymous))
