"""Decode the 512-byte ATA IDENTIFY DEVICE response.

Every ATA device returns this structure, and both platforms can obtain it: on
Linux from the ``vpd_pg89`` sysfs file with no privileges at all, or through
SG_IO passthrough; on Windows through ``IOCTL_ATA_PASS_THROUGH_DIRECT``.  The
bytes are identical either way, so this decoder serves both.

System Role:
    Pure adapter-layer decoding.  Takes bytes, returns domain values, touches
    nothing else.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import NamedTuple

from ....domain.enums import DiskKind
from ....domain.text import device_text

# Offset of the embedded IDENTIFY payload inside a SCSI ATA Information VPD page
# (page 0x89). The page header and the SAT fields occupy the first 60 bytes.
VPD_ATA_INFORMATION_IDENTIFY_OFFSET = 60

IDENTIFY_LENGTH = 512

# Word 76 advertises supported SATA signalling rates as a bitmap, one bit per
# generation. Word 77 reports the negotiated rate as a small integer in bits 3:1.
_SATA_GEN_GBPS: dict[int, float] = {1: 1.5, 2: 3.0, 3: 6.0}

# A nominal media rotation rate of 1 means the device declares itself solid
# state. Real rotation rates start at 0x0401.
_ROTATION_RATE_SSD = 1
_ROTATION_RATE_MIN_RPM = 0x0401
_ROTATION_RATE_MAX_RPM = 0xFFFE

# Word 106 bit 12 says the logical sector is LARGER than 256 words (512
# bytes), so a count of 256 or less in words 117-118 contradicts the bit that
# sent the decoder there. The ceiling is the 4Kn format, 2048 words (4096
# bytes), the largest logical sector an ATA drive ships with; the field itself
# can hold up to 0xFFFFFFFF words, and garbage there once multiplied a 4 TB
# drive into a 67-exabyte capacity. Outside this range the size is not known.
_MIN_LOGICAL_SECTOR_WORDS = 257
_MAX_LOGICAL_SECTOR_WORDS = 2048


@dataclass(frozen=True, slots=True)
class AtaIdentity:
    """What an ATA device says about itself.

    Attributes:
        model: Product name.
        serial: Serial number.
        firmware: Firmware revision.
        sectors: Addressable sector count, or ``None`` when not reported.
        sector_size: Logical sector size in bytes.
        kind: Solid state or rotating, from the nominal rotation rate.
        rotation_rpm: Rotation rate for a spinning drive, else ``None``.
        negotiated_gbps: Currently negotiated SATA rate.
        max_gbps: Fastest SATA rate the device supports.
        smart_supported: Whether the device implements the SMART feature set.

    Example:
        >>> AtaIdentity("ACME SSD 4TB", "SERIAL0", "FW1").model
        'ACME SSD 4TB'
    """

    model: str
    serial: str
    firmware: str
    sectors: int | None = None
    sector_size: int = 512
    kind: DiskKind = DiskKind.UNKNOWN
    rotation_rpm: int | None = None
    negotiated_gbps: float | None = None
    max_gbps: float | None = None
    smart_supported: bool = False

    @property
    def size_bytes(self) -> int | None:
        """Capacity in bytes, or ``None`` when the sector count is unknown.

        Example:
            >>> AtaIdentity("m", "s", "f", sectors=1000, sector_size=512).size_bytes
            512000
        """
        return None if self.sectors is None else self.sectors * self.sector_size


def _ata_string(words: tuple[int, ...], start: int, length: int) -> str:
    """Extract an ATA string field.

    ATA stores strings with the first character in the high byte of each 16-bit
    word, so reading the buffer as little-endian words and re-emitting each word
    big-endian restores the original order.  Skipping this step is why a model
    reads as ``aSsmnu gSS D78`` instead of ``Samsung SSD 870``.

    Args:
        words: The IDENTIFY response as 256 little-endian words.
        start: Index of the first word of the field.
        length: Field length in words.

    Returns:
        The trimmed ASCII string.

    Example:
        >>> _ata_string((0x4162, 0x6364), 0, 2)
        'Abcd'
    """
    raw = b"".join(struct.pack(">H", words[start + index]) for index in range(length))
    return device_text(raw.decode("ascii", errors="replace"))


class SataRates(NamedTuple):
    """A SATA link's negotiated rate and the highest it supports.

    Both are floats, so returned as a bare pair a swap type-checks perfectly and
    then reports a drive running at its maximum as one running below it, which
    is the single judgement this tool exists to make.

    Attributes:
        negotiated: The rate the link actually came up at, in Gb/s.
        maximum: The highest rate the drive supports, in Gb/s.
    """

    negotiated: float | None
    maximum: float | None


def _sata_rates(words: tuple[int, ...]) -> SataRates:
    """Return the negotiated and maximum SATA rates in Gb/s.

    Args:
        words: The IDENTIFY response as 256 little-endian words.

    Returns:
        A pair of negotiated rate and maximum supported rate, either of which
        may be ``None`` when the device does not report it.  Older drives leave
        the negotiated field at zero even though they report their capability,
        which is why the two are read independently.
    """
    capability = words[76]
    maximum: float | None = None
    if capability not in (0x0000, 0xFFFF):
        supported = [gbps for generation, gbps in _SATA_GEN_GBPS.items() if capability & (1 << generation)]
        maximum = max(supported) if supported else None

    negotiated = _SATA_GEN_GBPS.get((words[77] >> 1) & 0x07)
    return SataRates(negotiated, maximum)


class SectorGeometry(NamedTuple):
    """How many sectors a drive has and how big each one is.

    Both ints: swapped, a 4 KiB drive reports 4096 sectors of 512 bytes.

    Attributes:
        sectors: Addressable sector count.
        sector_size: Bytes per sector.
    """

    sectors: int | None
    sector_size: int


def _sector_geometry(words: tuple[int, ...]) -> SectorGeometry:
    """Return the addressable sector count and logical sector size.

    When word 106 claims a larger-than-512-byte logical sector but words 117
    and 118 hold a word count no real ATA device publishes, the sector SIZE is
    unknown, and so is every capacity built from it. The count is withheld
    (``sectors`` None, so ``AtaIdentity.size_bytes`` is None) rather than
    multiplied by a 512-byte guess: the drive said its sector is larger, so the
    guess would understate the capacity of exactly the drive that said so.
    """
    lba48 = words[100] | (words[101] << 16) | (words[102] << 32) | (words[103] << 48)
    lba28 = words[60] | (words[61] << 16)
    sectors = lba48 or lba28 or None

    sector_size = 512
    # Word 106 is valid only when bit 14 is set and bit 15 clear. Bit 12 then
    # says the logical sector is larger than 512 bytes, with the true size in
    # words 117 and 118, expressed in 16-bit words rather than bytes.
    geometry = words[106]
    if geometry & 0x4000 and not geometry & 0x8000 and geometry & 0x1000:
        logical_words = words[117] | (words[118] << 16)
        if _MIN_LOGICAL_SECTOR_WORDS <= logical_words <= _MAX_LOGICAL_SECTOR_WORDS:
            sector_size = logical_words * 2
        else:
            sectors = None
    return SectorGeometry(sectors, sector_size)


def _media_kind(words: tuple[int, ...]) -> tuple[DiskKind, int | None]:
    """Return the media kind and rotation rate from word 217."""
    rate = words[217]
    if rate == _ROTATION_RATE_SSD:
        return DiskKind.SSD, None
    if _ROTATION_RATE_MIN_RPM <= rate <= _ROTATION_RATE_MAX_RPM:
        return DiskKind.HDD, rate
    return DiskKind.UNKNOWN, None


def _refuse_an_unusable_page(page: bytes) -> None:
    """Refuse a page that cannot be an IDENTIFY answer.

    The word-255 signature is optional in ATA, and the checksum is not checked:
    the committed captures are scrubbed of their serials, which invalidates it,
    so enforcing it would refuse every fixture a recapture produces.

    Raises:
        ValueError: For an all-zero or all-ones page.
    """
    if not any(page) or page == b"\xff" * len(page):
        message = "IDENTIFY response is all zeros or all ones: it carries no identity"
        raise ValueError(message)


def decode_identify(blob: bytes) -> AtaIdentity:
    """Decode an ATA IDENTIFY DEVICE response.

    Args:
        blob: At least 512 bytes of IDENTIFY data.

    Returns:
        The device's self-description.

    Raises:
        ValueError: If the buffer is shorter than one IDENTIFY response, is
            all zeros or all ones, carries the optional 0xA5 signature with a
            wrong checksum, or names no model, serial or firmware and gives no
            LBA count. Such a page is what a non-ATA device's passthrough can
            return, and it identifies nothing.

    Example:
        >>> import struct
        >>> words = [0] * 256
        >>> words[27:31] = struct.unpack('<4H', b'aDkstiIVE   '[:8])
        >>> blob = struct.pack('<256H', *words)
        >>> decode_identify(blob).serial
        ''
    """
    if len(blob) < IDENTIFY_LENGTH:
        message = f"IDENTIFY response is {len(blob)} bytes, expected at least {IDENTIFY_LENGTH}"
        raise ValueError(message)

    page = blob[:IDENTIFY_LENGTH]
    _refuse_an_unusable_page(page)
    words = struct.unpack("<256H", page)
    negotiated, maximum = _sata_rates(words)
    sectors, sector_size = _sector_geometry(words)
    kind, rotation = _media_kind(words)
    model = _ata_string(words, 27, 20)
    serial = _ata_string(words, 10, 10)
    firmware = _ata_string(words, 23, 4)
    if not (model or serial or firmware or any(words[60:62]) or any(words[100:104])):
        message = "IDENTIFY response names no model, serial or firmware and gives no LBA count: it carries no identity"
        raise ValueError(message)

    return AtaIdentity(
        model=model,
        serial=serial,
        firmware=firmware,
        sectors=sectors,
        sector_size=sector_size,
        kind=kind,
        rotation_rpm=rotation,
        negotiated_gbps=negotiated,
        max_gbps=maximum,
        # Word 82 bit 0 declares the SMART feature set as supported.
        smart_supported=bool(words[82] & 0x0001) and words[82] not in (0x0000, 0xFFFF),
    )


def decode_vpd_ata_information(blob: bytes) -> AtaIdentity:
    """Decode the IDENTIFY payload embedded in a SCSI ATA Information VPD page.

    This is the unprivileged path on Linux: ``vpd_pg89`` is world readable, and
    it carries the IDENTIFY response even for drives behind a SAS HBA, where the
    ``ata_link`` sysfs class reports nothing useful.

    Args:
        blob: The whole VPD page 0x89 as read from sysfs.

    Returns:
        The device's self-description.

    Raises:
        ValueError: If the page is too short to contain an IDENTIFY response.

    Example:
        >>> decode_vpd_ata_information(b'')
        Traceback (most recent call last):
        ...
        ValueError: ATA Information VPD page is 0 bytes, expected at least 572
    """
    required = VPD_ATA_INFORMATION_IDENTIFY_OFFSET + IDENTIFY_LENGTH
    if len(blob) < required:
        message = f"ATA Information VPD page is {len(blob)} bytes, expected at least {required}"
        raise ValueError(message)
    return decode_identify(blob[VPD_ATA_INFORMATION_IDENTIFY_OFFSET:])


__all__ = [
    "IDENTIFY_LENGTH",
    "VPD_ATA_INFORMATION_IDENTIFY_OFFSET",
    "AtaIdentity",
    "decode_identify",
    "decode_vpd_ata_information",
]
