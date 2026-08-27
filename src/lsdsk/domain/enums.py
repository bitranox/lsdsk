"""Type-safe domain enums for output formats and deployment targets."""

from __future__ import annotations

from enum import StrEnum


class OutputFormat(StrEnum):
    """Output format options for configuration display.

    Defines valid output format choices for the config command.
    A StrEnum, so it compares equal to its value and formats as its value,
    which keeps Click integration and log output straightforward.

    Attributes:
        HUMAN: Human-readable TOML-like output format.
        JSON: Machine-readable JSON output format.

    Example:
        >>> OutputFormat.HUMAN.value
        'human'
        >>> OutputFormat.JSON == "json"
        True
    """

    HUMAN = "human"
    JSON = "json"


class DeployTarget(StrEnum):
    """Configuration deployment target layers.

    Defines valid target layers for configuration file deployment.
    A StrEnum, so it compares equal to its value and formats as its value,
    which keeps Click integration and log output straightforward.

    Attributes:
        APP: System-wide application configuration (requires privileges).
        HOST: System-wide host-specific configuration (requires privileges).
        USER: User-specific configuration (~/.config on Linux).

    Example:
        >>> DeployTarget.USER.value
        'user'
        >>> DeployTarget.APP == "app"
        True
    """

    APP = "app"
    HOST = "host"
    USER = "user"


class Severity(StrEnum):
    """How much a diagnostic finding should worry the reader.

    Ordered from most to least urgent.  ``HINT`` covers the case where a device
    runs below its own capability but the machine cannot do better, so there is
    nothing to fix without changing hardware.

    Attributes:
        CRITICAL: Data loss or an entirely dead link is in play.
        WARNING: Actionable: something on this machine can be changed to fix it.
        HINT: Informational ceiling, actionable only by replacing hardware.

    Example:
        >>> Severity.WARNING.value
        'warning'
        >>> Severity.CRITICAL == "critical"
        True
    """

    CRITICAL = "critical"
    WARNING = "warning"
    HINT = "hint"


class BusType(StrEnum):
    """Transport a disk speaks to its controller.

    Attributes:
        SATA: Serial ATA, whether on an AHCI port or tunnelled through SAS.
        SAS: Native Serial Attached SCSI.
        NVME: NVM Express over PCIe.
        USB: USB mass storage or UAS.
        VIRTUAL: A device with no physical link, because there is no
            hardware behind it: a hypervisor's disk, or one the kernel
            provides itself such as zram, loop or a zvol.
        UNKNOWN: Could not be determined.

    Example:
        >>> BusType.SATA.value
        'sata'
    """

    SATA = "sata"
    SAS = "sas"
    NVME = "nvme"
    USB = "usb"
    VIRTUAL = "virtual"
    UNKNOWN = "unknown"


class DiskKind(StrEnum):
    """Whether a disk has spinning platters.

    Attributes:
        SSD: Solid state, reported as a rotation rate of 1.
        HDD: Rotating media, reported as its RPM.
        UNKNOWN: The device did not report a rotation rate.

    Example:
        >>> DiskKind.SSD.value
        'ssd'
    """

    SSD = "ssd"
    HDD = "hdd"
    UNKNOWN = "unknown"


class ControllerKind(StrEnum):
    """What sort of storage controller this is, from its PCI class code.

    Attributes:
        AHCI: A SATA controller in AHCI mode (PCI class 0x0106).
        SAS: A serial attached SCSI host bus adapter (PCI class 0x0107).
        NVME: An NVM Express controller (PCI class 0x0108).
        RAID: A RAID controller that hides its members (PCI class 0x0104).
        IDE: A legacy parallel ATA controller (PCI class 0x0101).
        OTHER: Storage class, but none of the above.
        UNKNOWN: Not classified.

    Example:
        >>> ControllerKind.SAS.value
        'sas'
    """

    AHCI = "ahci"
    SAS = "sas"
    NVME = "nvme"
    RAID = "raid"
    IDE = "ide"
    OTHER = "other"
    UNKNOWN = "unknown"


class Environment(StrEnum):
    """What kind of machine the readings came from.

    It changes what they mean. A container sees the host's hardware through a
    shared kernel but usually cannot interrogate or touch it; a virtual machine
    sees whatever the hypervisor invented.

    Attributes:
        BARE_METAL: Real hardware this machine owns.
        VIRTUAL_MACHINE: A guest, so the disks are the hypervisor's.
        CONTAINER: A container, so the hardware belongs to the host.
        UNKNOWN: Could not be determined.

    Example:
        >>> Environment.CONTAINER.value
        'container'
    """

    BARE_METAL = "bare_metal"
    VIRTUAL_MACHINE = "virtual_machine"
    CONTAINER = "container"
    UNKNOWN = "unknown"


class Platform(StrEnum):
    """An operating system lsdsk has a reader for.

    Kept here rather than as loose string constants because it is a closed set
    that crosses a boundary in both directions: a capture records the platform
    it was taken on, and a replay of that capture dispatches on it. Two sides of
    one contract held together by convention drift apart silently.

    Attributes:
        LINUX: Value ``sys.platform`` reports on Linux.
        WINDOWS: Value ``sys.platform`` reports on Windows, including 64-bit.

    Example:
        >>> Platform("linux")
        <Platform.LINUX: 'linux'>
        >>> f"{Platform.WINDOWS}"
        'win32'
    """

    LINUX = "linux"
    WINDOWS = "win32"


class Align(StrEnum):
    """Which way a table column's text is set.

    A bare string here fell through to the left on any typo, silently, because
    the renderer tested ``align == "right"`` and treated everything else as the
    default. There are exactly two answers and this names them.

    Example:
        >>> f"{Align.RIGHT}"
        'right'
    """

    LEFT = "left"
    RIGHT = "right"


class CliCommand(StrEnum):
    """A command name, as it appears in the machine-readable envelope.

    The envelope names the command that produced it, so a consumer can tell one
    output from another. A literal baked into the shared builder made every
    command claim to be ``scan``.

    Example:
        >>> f"{CliCommand.DISKS}"
        'disks'
    """

    TOPOLOGY = "topology"
    CONTROLLERS = "controllers"
    DISKS = "disks"
    HEALTH = "health"
    SMART = "smart"
    FINDINGS = "findings"
    SLOTS = "slots"
    TREND = "trend"


__all__ = [
    "Align",
    "BusType",
    "CliCommand",
    "ControllerKind",
    "DeployTarget",
    "DiskKind",
    "Environment",
    "OutputFormat",
    "Platform",
    "Severity",
]
