"""Decode NVMe Identify Controller and the SMART/Health log page.

Linux obtains both through ``NVME_IOCTL_ADMIN_CMD``; Windows obtains the same
payloads through ``IOCTL_STORAGE_QUERY_PROPERTY`` and
``IOCTL_STORAGE_PROTOCOL_COMMAND``.  The structures are defined by the NVMe
specification, not by the operating system, so this decoder serves both.

System Role:
    Pure adapter-layer decoding.  Takes bytes, returns domain values.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ....domain.models import Health
from .text import device_text

IDENTIFY_CONTROLLER_LENGTH = 4096
SMART_LOG_LENGTH = 512

# NVMe reports temperatures in Kelvin.
_KELVIN_OFFSET = 273

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
    """Convert a Kelvin reading to Celsius, treating zero as not reported."""
    return None if kelvin == 0 else kelvin - _KELVIN_OFFSET


def decode_identify_controller(blob: bytes) -> NvmeIdentity:
    """Decode an NVMe Identify Controller data structure.

    Args:
        blob: At least 4096 bytes of Identify Controller data.

    Returns:
        The controller's self-description.

    Raises:
        ValueError: If the buffer is shorter than one Identify response.

    Example:
        >>> decode_identify_controller(bytes(4096)).model
        ''
    """
    if len(blob) < IDENTIFY_CONTROLLER_LENGTH:
        message = f"Identify Controller is {len(blob)} bytes, expected at least {IDENTIFY_CONTROLLER_LENGTH}"
        raise ValueError(message)

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
        The drive's health.

    Raises:
        ValueError: If the buffer is shorter than one log page.

    Example:
        >>> health = decode_smart_log(bytes(512))
        >>> health.percent_used
        0
        >>> health.ok
        True
    """
    if len(blob) < SMART_LOG_LENGTH:
        message = f"SMART log is {len(blob)} bytes, expected at least {SMART_LOG_LENGTH}"
        raise ValueError(message)

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
