"""Decode NVMe Identify Controller and the SMART/Health log page.

Linux obtains both through ``NVME_IOCTL_ADMIN_CMD``; Windows obtains the same
payloads through ``IOCTL_STORAGE_QUERY_PROPERTY`` (a protocol-specific property
query).  The structures are defined by the NVMe
specification, not by the operating system, so this decoder serves both.

System Role:
    Pure adapter-layer decoding.  Takes bytes, returns domain values.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ....domain.models import Health
from ....domain.text import device_text
from .captured import plausible_celsius, refuse_a_floating_page

IDENTIFY_CONTROLLER_LENGTH = 4096
SMART_LOG_LENGTH = 512

# NVMe reports temperatures in Kelvin.
_KELVIN_OFFSET = 273

# Identify Controller text fields (SN, MN, FR) are ASCII strings padded with
# spaces; a NUL is tolerated as padding too. Anything else was never published.
_ASCII_PRINTABLE = range(0x20, 0x7F)
_TEXT_FIELDS = ((4, 20), (24, 40), (64, 8))

# SMART / Health log, byte 0: bits 0-5 are defined (spare, temperature,
# reliability, read-only, volatile backup, persistent memory region - NVM
# Express Base Specification 1.4 and 2.0, Figure "SMART / Health Information
# Log"); bits 7:6 are reserved, so a set one means the page is noise.
_CRITICAL_WARNING_RESERVED_BITS = 0xC0

# Available Spare (byte 3) is a percentage.
_PERCENT_MAX = 100

# Data units in the SMART log count thousands of 512-byte units.
_DATA_UNIT_BYTES = 512 * 1000


@dataclass(frozen=True, slots=True)
class NvmeIdentity:
    """What an NVMe controller says about itself.

    Attributes:
        model: Product name.
        serial: Serial number.
        firmware: Firmware revision.
        warning_temperature_c: Vendor's composite warning threshold.
        critical_temperature_c: Vendor's composite critical threshold.

    Example:
        >>> NvmeIdentity("ACME NVMe 2TB", "SERIAL0", "FW1").model
        'ACME NVMe 2TB'
    """

    model: str
    serial: str
    firmware: str
    warning_temperature_c: int | None = None
    critical_temperature_c: int | None = None


def _ascii_field(blob: bytes, start: int, length: int) -> str:
    """Extract a fixed-width ASCII field, which NVMe pads with spaces."""
    return device_text(blob[start : start + length].decode("ascii", errors="replace"))


def _kelvin_to_celsius(kelvin: int) -> int | None:
    """Convert a Kelvin reading to Celsius, treating zero or an implausible figure as not reported."""
    return None if kelvin == 0 else plausible_celsius(kelvin - _KELVIN_OFFSET)


def _refuse_a_non_ascii_text_field(blob: bytes) -> None:
    """Refuse an Identify page whose serial, model or firmware holds a non-ASCII byte.

    Raises:
        ValueError: For a text field no controller could have published, which
            is noise with a few bytes changed, as an all-ones check cannot see.
    """
    for start, length in _TEXT_FIELDS:
        if any(byte and byte not in _ASCII_PRINTABLE for byte in blob[start : start + length]):
            message = f"Identify Controller text field at byte {start} is not ASCII: it is not an Identify answer"
            raise ValueError(message)


def _refuse_an_implausible_health_field(blob: bytes) -> None:
    """Refuse a SMART log whose spare percentage or warning bits could not be published.

    Raises:
        ValueError: For an available spare above 100 percent or a reserved
            critical-warning bit set.
    """
    if blob[3] > _PERCENT_MAX or blob[0] & _CRITICAL_WARNING_RESERVED_BITS:
        message = "SMART log carries a value the specification does not allow: it is not a health log"
        raise ValueError(message)


def decode_identify_controller(blob: bytes) -> NvmeIdentity:
    """Decode an NVMe Identify Controller data structure.

    Args:
        blob: At least 4096 bytes of Identify Controller data.

    Returns:
        The controller's self-description.

    Raises:
        ValueError: If the buffer is shorter than one Identify response or is
            all ones, which is what a bus with nothing on it answers, or if a
            serial, model or firmware field holds a byte outside ASCII.

    Example:
        >>> decode_identify_controller(bytes(4096)).model
        ''
    """
    if len(blob) < IDENTIFY_CONTROLLER_LENGTH:
        message = f"Identify Controller is {len(blob)} bytes, expected at least {IDENTIFY_CONTROLLER_LENGTH}"
        raise ValueError(message)
    refuse_a_floating_page(blob[:IDENTIFY_CONTROLLER_LENGTH], what="Identify Controller")
    _refuse_a_non_ascii_text_field(blob)

    warning, critical = struct.unpack_from("<HH", blob, 266)
    return NvmeIdentity(
        model=_ascii_field(blob, 24, 40),
        serial=_ascii_field(blob, 4, 20),
        firmware=_ascii_field(blob, 64, 8),
        warning_temperature_c=_kelvin_to_celsius(warning),
        critical_temperature_c=_kelvin_to_celsius(critical),
    )


def decode_smart_log(blob: bytes, identity: NvmeIdentity | None = None) -> Health:
    """Decode the NVMe SMART/Health information log page.

    Args:
        blob: At least 512 bytes of log page 0x02.
        identity: The controller identity, which carries the vendor's own
            temperature thresholds; without it those stay ``None``.

    Returns:
        The drive's health, or a health with no reading in it when the page is
        all zeros: no drive reports a composite temperature of zero kelvin, so
        such a page is a command that moved nothing, and reading it as a drive
        with no wear and no errors would be the opposite of what happened. The
        ATA decoder answers an empty table the same way.

    Raises:
        ValueError: If the buffer is shorter than one log page or the page is
            all ones: a floating bus answers that way, and decoding it would
            report a failing drive at 255 percent wear. Also if the available
            spare exceeds 100 percent or a reserved critical-warning bit is
            set, which is the same noise with a few bytes changed.

    Example:
        >>> health = decode_smart_log(bytes([0, 0, 0, 100]) + bytes(508))
        >>> health.percent_used
        0
        >>> health.ok
        True
        >>> decode_smart_log(bytes(512)).ok is None
        True
    """
    if len(blob) < SMART_LOG_LENGTH:
        message = f"SMART log is {len(blob)} bytes, expected at least {SMART_LOG_LENGTH}"
        raise ValueError(message)

    refuse_a_floating_page(blob[:SMART_LOG_LENGTH], what="SMART log")
    if not any(blob[:SMART_LOG_LENGTH]):
        return Health()
    _refuse_an_implausible_health_field(blob)
    critical_warning = blob[0]
    composite_kelvin = struct.unpack_from("<H", blob, 1)[0]
    return Health(
        ok=critical_warning == 0,
        temperature_c=_kelvin_to_celsius(composite_kelvin),
        temperature_warning_c=None if identity is None else identity.warning_temperature_c,
        temperature_critical_c=None if identity is None else identity.critical_temperature_c,
        power_on_hours=int.from_bytes(blob[128:144], "little"),
        percent_used=blob[5],
        media_errors=int.from_bytes(blob[160:176], "little"),
        bytes_read=int.from_bytes(blob[32:48], "little") * _DATA_UNIT_BYTES,
        bytes_written=int.from_bytes(blob[48:64], "little") * _DATA_UNIT_BYTES,
        power_cycles=int.from_bytes(blob[112:128], "little"),
        unsafe_shutdowns=int.from_bytes(blob[144:160], "little"),
        error_log_entries=int.from_bytes(blob[176:192], "little"),
        available_spare=blob[3],
        available_spare_threshold=blob[4],
        critical_warning=critical_warning,
    )


__all__ = [
    "IDENTIFY_CONTROLLER_LENGTH",
    "SMART_LOG_LENGTH",
    "NvmeIdentity",
    "decode_identify_controller",
    "decode_smart_log",
]
