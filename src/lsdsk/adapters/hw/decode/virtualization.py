"""Decide whether this machine is bare metal, a virtual machine, or a container.

It changes what the readings mean, so it is not cosmetic.

In a container the kernel is the host's, so ``/sys`` shows the host's disks and
controllers in full detail while the device nodes needed to interrogate them may
not exist at all. The topology is real; it simply belongs to the host, which is
where anything found has to be investigated and changed.

In a virtual machine the disks are the hypervisor's invention. Link speeds,
temperatures and SMART data are whatever it chose to present, so a "SATA 6 Gb/s"
reading describes an emulated controller rather than a cable.

Classification is pure so it can be tested for every environment from any one of
them; the platform readers only gather the raw strings.

System Role:
    Adapter layer, pure decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from ....domain.enums import Environment

# Container runtimes, matched against the ``container=`` variable in PID 1's
# environment and against control-group paths.
_CONTAINER_MARKERS: dict[str, str] = {
    "lxc": "LXC",
    "docker": "Docker",
    "podman": "Podman",
    "systemd-nspawn": "systemd-nspawn",
    "oci": "OCI",
    "containerd": "containerd",
    "kubepods": "Kubernetes",
}

# Hypervisors, matched against DMI vendor and product strings. The values a
# hypervisor writes into DMI are the most reliable signal a guest has.
_HYPERVISOR_MARKERS: dict[str, str] = {
    "qemu": "QEMU",
    "kvm": "KVM",
    "vmware": "VMware",
    "virtualbox": "VirtualBox",
    "innotek": "VirtualBox",
    "xen": "Xen",
    "bochs": "QEMU",
    "parallels": "Parallels",
    "hyper-v": "Hyper-V",
    "microsoft corporation": "Hyper-V",
    "virtual machine": "Hyper-V",
    "amazon ec2": "Amazon EC2",
    "google": "Google Compute Engine",
    "openstack": "OpenStack",
    "apple virtualization": "Apple Virtualization",
}


# Filesystems that indicate a container, but only where they are mounted.
_CONTAINER_FILESYSTEMS: dict[str, str] = {"fuse.lxcfs": "lxc", "lxcfs": "lxc"}

# Mount points that only a container has its own system paths overlaid at.
_CONTAINED_MOUNT_POINTS = ("/proc/", "/sys/devices/system/cpu")


def container_markers_in_mounts(mountinfo: str) -> str:
    """Extract container evidence from a mount table.

    Judged by where a filesystem is mounted, never by its mere presence. A
    machine that HOSTS containers runs the same filesystems: a Proxmox server
    mounts lxcfs at ``/var/lib/lxcfs`` to serve its guests, and a Docker host
    has overlay mounts under ``/var/lib/docker``. Only inside a container is
    such a filesystem mounted over the system's own paths, replacing ``/proc``
    entries so the guest sees its own limits.

    Matching the filesystem name anywhere in the table classified every
    container host as a container, which is precisely backwards.

    Args:
        mountinfo: The contents of ``/proc/self/mountinfo``.

    Returns:
        A space-separated set of runtime names, empty when none applies.

    Example:
        >>> inside = "6226 6210 0:99 /proc/cpuinfo /proc/cpuinfo rw - fuse.lxcfs lxcfs rw"
        >>> container_markers_in_mounts(inside)
        'lxc'

    The same filesystem on a host that merely serves containers is not evidence:

        >>> host = "412 33 0:62 / /var/lib/lxcfs rw - fuse.lxcfs lxcfs rw"
        >>> container_markers_in_mounts(host)
        ''
        >>> container_markers_in_mounts("")
        ''
    """
    found: set[str] = set()
    for line in mountinfo.splitlines():
        fields = line.split(" - ", 1)
        if len(fields) != 2:  # noqa: PLR2004 - a mountinfo line has exactly one separator
            continue
        before, after = fields
        columns = before.split()
        if len(columns) < 5:  # noqa: PLR2004 - id, parent, device, root, mount point
            continue
        mount_point = columns[4]
        filesystem = after.split()[0] if after.split() else ""
        runtime = _CONTAINER_FILESYSTEMS.get(filesystem)
        if runtime and mount_point.startswith(_CONTAINED_MOUNT_POINTS):
            found.add(runtime)
    return " ".join(sorted(found))


@dataclass(frozen=True, slots=True)
class VirtualizationEvidence:
    """The raw strings a platform reader gathers for classification.

    A capture's ``environment`` section is parsed straight into this, for a live
    run and a replay alike, so a value of the wrong type is refused where the
    reading enters rather than guessed at here.

    Attributes:
        container_marker: The ``container=`` value from PID 1's environment, or
            a runtime name found another way.
        container_files: Marker files that were present, such as ``/.dockerenv``.
        cgroup: PID 1's control-group path.
        dmi_vendor: System vendor from DMI.
        dmi_product: Product name from DMI.
        dmi_board_vendor: Baseboard vendor from DMI.
        dmi_board_name: Baseboard model from DMI, the name somebody would shop for.
        hypervisor_flag: Whether the CPU reports running under a hypervisor.
        hypervisor_type: The Xen-style hypervisor type file, when present.
        mount_markers: Mount table entries that name a container filesystem,
            such as lxcfs. This is the signal that survives when PID 1's
            environment is unreadable, which it is for an ordinary user.
    """

    container_marker: str = ""
    container_files: tuple[str, ...] = ()
    cgroup: str = ""
    mount_markers: str = ""
    dmi_vendor: str = ""
    dmi_product: str = ""
    dmi_board_vendor: str = ""
    dmi_board_name: str = ""
    hypervisor_flag: bool = False
    hypervisor_type: str = ""


def _match(haystack: str, markers: dict[str, str]) -> str | None:
    """Return the first marker name whose key appears in the text."""
    lowered = haystack.lower()
    return next((name for key, name in markers.items() if key in lowered), None)


class Classification(NamedTuple):
    """What kind of machine this is, and the evidence for saying so.

    Named because ``Environment`` is a ``StrEnum`` and therefore IS a ``str``:
    returned as a bare pair, swapping the two type-checks perfectly, and two
    separate platform builders unpack it positionally.

    Attributes:
        environment: Bare metal, a virtual machine or a container.
        detail: The evidence, for the caveat line the report prints.
    """

    environment: Environment
    detail: str


def classify(evidence: VirtualizationEvidence) -> Classification:
    """Decide what kind of machine this is, and name it.

    A container is reported even when the host underneath is itself virtual,
    because the container is the nearer boundary: whatever the host turns out to
    be, the storage on show is not this machine's.

    Args:
        evidence: The raw strings gathered by a platform reader.

    Returns:
        The environment and a short description, empty when nothing was found.

    Example:
        >>> classify(VirtualizationEvidence(container_marker="lxc"))
        Classification(environment=<Environment.CONTAINER: 'container'>, detail='LXC')

    An ordinary user cannot read PID 1's environment, so the mount table is what
    identifies a container for them, and it must reach the same verdict:

        >>> classify(VirtualizationEvidence(mount_markers="lxcfs /proc/cpuinfo",
        ...                                 dmi_vendor="Micro-Star International Co., Ltd."))
        Classification(environment=<Environment.CONTAINER: 'container'>, detail='LXC')
        >>> classify(VirtualizationEvidence(dmi_vendor="QEMU", hypervisor_flag=True))
        Classification(environment=<Environment.VIRTUAL_MACHINE: 'virtual_machine'>, detail='QEMU')
        >>> classify(VirtualizationEvidence(dmi_vendor="Micro-Star International Co., Ltd."))
        Classification(environment=<Environment.BARE_METAL: 'bare_metal'>, detail='')
    """
    container = (
        _match(evidence.container_marker, _CONTAINER_MARKERS)
        or _match(evidence.cgroup, _CONTAINER_MARKERS)
        or _match(evidence.mount_markers, _CONTAINER_MARKERS)
    )
    if container is None and evidence.container_files:
        container = _match(" ".join(evidence.container_files), _CONTAINER_MARKERS) or "container"
    if container is not None:
        return Classification(Environment.CONTAINER, container)

    hypervisor = (
        _match(evidence.hypervisor_type, _HYPERVISOR_MARKERS)
        or _match(evidence.dmi_vendor, _HYPERVISOR_MARKERS)
        or _match(evidence.dmi_product, _HYPERVISOR_MARKERS)
    )
    if hypervisor is not None:
        return Classification(Environment.VIRTUAL_MACHINE, hypervisor)
    if evidence.hypervisor_flag or evidence.hypervisor_type:
        # The CPU says it is virtualised but nothing identified the hypervisor,
        # which is normal when a guest is deliberately given host DMI strings.
        return Classification(Environment.VIRTUAL_MACHINE, "")
    if evidence.dmi_vendor or evidence.dmi_product:
        return Classification(Environment.BARE_METAL, "")
    return Classification(Environment.UNKNOWN, "")


def board_name(evidence: VirtualizationEvidence) -> str:
    """Name the mainboard from the DMI strings a reader recorded.

    The baseboard fields carry the model somebody would shop for; the system
    product name is often only an internal code ("MS-7D27" where the board is
    "MEG Z690 ACE"). Vendor and model are joined only when the model does not
    already repeat the vendor, which many boards do.

    Args:
        evidence: What a reader gathered.

    Returns:
        The board name, or an empty string when DMI carried none.

    Example:
        >>> board_name(VirtualizationEvidence(
        ...     dmi_board_vendor="Micro-Star International Co., Ltd.", dmi_board_name="MEG Z690 ACE (MS-7D27)"))
        'Micro-Star International Co., Ltd. MEG Z690 ACE (MS-7D27)'
        >>> board_name(VirtualizationEvidence(dmi_board_name="PRIME B450M"))
        'PRIME B450M'
        >>> board_name(VirtualizationEvidence(dmi_board_vendor="ASUS", dmi_board_name="ASUS X570"))
        'ASUS X570'
        >>> board_name(VirtualizationEvidence())
        ''
    """
    vendor_text = evidence.dmi_board_vendor.strip()
    model_text = evidence.dmi_board_name.strip()
    if not model_text:
        return vendor_text
    # A vendor such as ", Inc." has no word before its first comma, and then
    # there is nothing the model could be repeating.
    leading = vendor_text.split(",")[0].split()
    first_word = leading[0] if leading else ""
    if first_word and first_word.lower() in model_text.lower():
        return model_text
    return f"{vendor_text} {model_text}".strip()


__all__ = ["VirtualizationEvidence", "board_name", "classify", "container_markers_in_mounts"]
