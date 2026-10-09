"""Decode the AHCI host controller capability registers.

An AHCI controller publishes what its ports can do in its own first register,
and nothing else does. ``libata`` fills ``sata_spd_max`` and
``hw_sata_spd_limit`` in sysfs only once a speed limit has actually been applied
to a link, so on a healthy machine both read empty and the port capability is
simply absent. Without it a drive running at 3 Gb/s cannot be told apart from a
drive whose port only offers 3 Gb/s, which are different situations with
different remedies.

The registers are memory mapped rather than in configuration space, so reading
them needs the controller's BAR and therefore root. The transport lives in the
platform reader; only the bit decoding is here.

Reference: Serial ATA AHCI Specification, section 3.1, HBA Capabilities.

System Role:
    Pure adapter-layer decoding.
"""

from __future__ import annotations

from dataclasses import dataclass

# Offsets of the two registers worth reading, from the start of the AHCI memory
# region.
CAPABILITY_OFFSET = 0x00
PORTS_IMPLEMENTED_OFFSET = 0x0C
REGISTER_SPAN = 0x10

# Bits 23:20 of the capability register hold the Interface Speed Support field.
_SPEED_SHIFT = 20
_SPEED_MASK = 0xF
INTERFACE_SPEEDS: dict[int, float] = {1: 1.5, 2: 3.0, 3: 6.0}

# Bits 4:0 hold the number of ports, counted from zero.
_PORT_COUNT_MASK = 0x1F

#: The PCI base and sub class of a SATA controller in AHCI mode, ``0x0106``.
AHCI_CLASS = 0x0106
#: The Linux driver that owns an AHCI register file. Firmware set to RAID mode
#: gives the same silicon class ``0x0104``, and ``ahci`` still drives it.
AHCI_DRIVER = "ahci"


def holds_ahci_registers(class_and_subclass: int | None, driver: str | None) -> bool:
    """Whether a PCI function is an AHCI controller, so its port count is the bitmap's.

    Either the bound driver or the class says so. The class alone missed every
    controller the firmware's RAID setting re-classes, and those were then
    counted from the ports ``libata`` declares; the driver alone would miss a
    controller whose driver was not read.

    Args:
        class_and_subclass: The PCI base and sub class, ``class >> 8``.
        driver: The name of the bound driver, or ``None`` when none was read.

    Returns:
        ``True`` when the function's ports are counted from its own bitmap.

    Example:
        >>> holds_ahci_registers(0x0104, "ahci")
        True
        >>> holds_ahci_registers(0x0106, None)
        True
        >>> holds_ahci_registers(0x0104, "megaraid_sas")
        False
    """
    return driver == AHCI_DRIVER or class_and_subclass == AHCI_CLASS


@dataclass(frozen=True, slots=True)
class AhciCapabilities:
    """What an AHCI controller says about itself.

    Attributes:
        interface_speed_gbps: The fastest rate its ports support.
        ports_implemented: How many ports are actually wired up.
        ports_declared: The port count field, which is often larger.

    Example:
        >>> AhciCapabilities(6.0, 2, 6).interface_speed_gbps
        6.0
    """

    interface_speed_gbps: float | None
    ports_implemented: int | None
    ports_declared: int


def decode_capabilities(capability: int, ports_implemented: int) -> AhciCapabilities:
    """Decode the two AHCI registers that describe a controller's ports.

    The implemented-ports bitmap is preferred over the declared count because
    the two disagree on real hardware: a chipset controller commonly declares
    six ports while wiring up two, and reporting four free ports that do not
    physically exist invites someone to go looking for them.

    Some firmware leaves the bitmap at zero, which the kernel treats as "assume
    they all exist". That case is reported as unknown rather than as zero, so
    the caller falls back instead of claiming a controller has no ports.

    Args:
        capability: The HBA capability register.
        ports_implemented: The ports-implemented bitmap.

    Returns:
        The decoded capabilities.

    Example:
        >>> decode_capabilities(0xE730FF45, 0x3)
        AhciCapabilities(interface_speed_gbps=6.0, ports_implemented=2, ports_declared=6)
        >>> decode_capabilities(0xE730FF45, 0x0).ports_implemented is None
        True
        >>> decode_capabilities(0x00100000, 0x1).interface_speed_gbps
        1.5
        >>> decode_capabilities(0x00000000, 0x1).interface_speed_gbps is None
        True
    """
    speed = INTERFACE_SPEEDS.get((capability >> _SPEED_SHIFT) & _SPEED_MASK)
    implemented = bin(ports_implemented).count("1") or None
    return AhciCapabilities(
        interface_speed_gbps=speed,
        ports_implemented=implemented,
        ports_declared=(capability & _PORT_COUNT_MASK) + 1,
    )


__all__ = [
    "AHCI_CLASS",
    "AHCI_DRIVER",
    "CAPABILITY_OFFSET",
    "INTERFACE_SPEEDS",
    "PORTS_IMPLEMENTED_OFFSET",
    "REGISTER_SPAN",
    "AhciCapabilities",
    "decode_capabilities",
    "holds_ahci_registers",
]
