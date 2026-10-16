"""A container runtime is named by a whole word of the evidence, never by its letters.

``classify`` reads PID 1's ``container=`` value and its control-group path for a
runtime's name. A bare substring test calls a host whose PID 1 sits in a unit
named ``associated.service`` an OCI container, because "oci" is three letters
many words carry, and a machine called a container skips every link, port and
fabric rule. The hypervisor DMI markers were fixed the same way.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode.virtualization import VirtualizationEvidence, classify
from lsdsk.domain.enums import Environment

# Each marker, a word that carries it inside another word, and what a real
# runtime writes for it into a control-group path.
_MARKERS = [
    pytest.param("lxc", "flxcode", "0::/lxc.payload.101/init.scope", "LXC", id="lxc"),
    pytest.param("docker", "dockerize", "0::/system.slice/docker-0123abcd.scope", "Docker", id="docker"),
    pytest.param(
        "podman",
        "podmanager",
        "0::/user.slice/user-1000.slice/user@1000.service/user.slice/podman-1234.scope",
        "Podman",
        id="podman",
    ),
    pytest.param(
        "systemd-nspawn",
        "systemd-nspawner",
        "0::/machine.slice/systemd-nspawn@web.service/payload",
        "systemd-nspawn",
        id="systemd-nspawn",
    ),
    pytest.param("oci", "associated", "0::/oci/0123abcd", "OCI", id="oci"),
    pytest.param("containerd", "containerdisk", "0::/containerd/0123abcd", "containerd", id="containerd"),
    pytest.param("kubepods", "kubepodstats", "0::/kubepods/besteffort/pod0123/abcd", "Kubernetes", id="kubepods"),
]


@pytest.mark.parametrize(("marker", "word", "path", "detail"), _MARKERS)
def test_a_cgroup_path_carrying_a_marker_inside_another_word_is_no_container(
    marker: str, word: str, path: str, detail: str
) -> None:
    """A unit whose name merely contains a runtime's letters is a host's own unit."""
    assert marker in word
    cgroup = f"0::/system.slice/{word}.service"

    verdict = classify(VirtualizationEvidence(cgroup=cgroup, dmi_vendor="ACME"))

    assert verdict == (Environment.BARE_METAL, ""), (cgroup, verdict, path, detail)


@pytest.mark.parametrize(("marker", "word", "path", "detail"), _MARKERS)
def test_a_container_value_carrying_a_marker_inside_another_word_is_no_container(
    marker: str, word: str, path: str, detail: str
) -> None:
    """The ``container=`` value is judged by its words too, not by its letters."""
    assert marker in word

    verdict = classify(VirtualizationEvidence(container_marker=word, dmi_vendor="ACME"))

    assert verdict == (Environment.BARE_METAL, ""), (word, verdict, path, detail)


@pytest.mark.parametrize(("marker", "word", "path", "detail"), _MARKERS)
def test_a_cgroup_path_a_runtime_writes_still_names_it(marker: str, word: str, path: str, detail: str) -> None:
    """The control: the separators a runtime writes around its name still bound it."""
    verdict = classify(VirtualizationEvidence(cgroup=path, dmi_vendor="ACME"))

    assert verdict == (Environment.CONTAINER, detail), (marker, word, verdict)


@pytest.mark.parametrize(("marker", "word", "path", "detail"), _MARKERS)
def test_a_container_value_naming_a_runtime_still_names_it(marker: str, word: str, path: str, detail: str) -> None:
    """The control for PID 1's environment: the runtime's own value names it."""
    verdict = classify(VirtualizationEvidence(container_marker=marker, dmi_vendor="ACME"))

    assert verdict == (Environment.CONTAINER, detail), (word, path, verdict)


def test_a_compound_container_value_names_its_runtime() -> None:
    """The LXC driver of libvirt writes ``lxc-libvirt``, which is still LXC."""
    assert classify(VirtualizationEvidence(container_marker="lxc-libvirt")) == (Environment.CONTAINER, "LXC")


def test_a_pid_one_in_init_scope_is_no_container() -> None:
    """What every committed Linux capture records for PID 1 names no runtime."""
    verdict = classify(VirtualizationEvidence(cgroup="0::/init.scope", dmi_vendor="ACME"))

    assert verdict == (Environment.BARE_METAL, "")
