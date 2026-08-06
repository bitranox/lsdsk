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


def capabilities_from_capture(raw: object) -> AhciCapabilities | None:
    """Rebuild what a reader recorded for one controller.

    Args:
        raw: The ``ahci`` mapping a reader stored, if any.

    Returns:
        The capabilities, or ``None`` when the controller has none recorded.

    Example:
        >>> capabilities_from_capture({"capability": 0xE730FF45, "ports_implemented": 3})
        AhciCapabilities(interface_speed_gbps=6.0, ports_implemented=2, ports_declared=6)
        >>> capabilities_from_capture(None) is None
        True
    """
    if not isinstance(raw, dict):
        return None
    values: dict[str, object] = {str(key): value for key, value in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] - a capture is JSON, so its mappings are Any
    capability = values.get("capability")
    implemented = values.get("ports_implemented")
    if not isinstance(capability, int) or not isinstance(implemented, int):
        return None
    return decode_capabilities(capability, implemented)


__all__ = [
    "CAPABILITY_OFFSET",
    "INTERFACE_SPEEDS",
    "PORTS_IMPLEMENTED_OFFSET",
    "REGISTER_SPAN",
    "AhciCapabilities",
    "capabilities_from_capture",
    "decode_capabilities",
]
