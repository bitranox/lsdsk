"""The ATA commands lsdsk issues, and the SCSI block that carries one through a translator.

A SATA drive is not always reached by the ATA transport. Behind a USB bridge or a
SAS host bus adapter it is a SCSI device, and the only way to ask it an ATA
question - IDENTIFY, SMART READ DATA - is to wrap the ATA registers in the SCSI
ATA PASS-THROUGH command that SAT (SCSI / ATA Translation, T10 SAT-4) defines,
which the translator unwraps and issues to the drive. Linux sends it through
``SG_IO`` and Windows through ``IOCTL_SCSI_PASS_THROUGH``; the command block is
the same bytes on both, so it is built here once, pure, and tested on every
runner.

Both readers issue the same three read-only ATA commands, so their codes are
named here once as well.

Contents:
    * :data:`ATA_IDENTIFY_DEVICE`, :data:`ATA_SMART`, :data:`SMART_READ_DATA`,
      :data:`SMART_READ_THRESHOLDS`, :data:`SMART_LBA_SIGNATURE` - the ATA side
    * :func:`ata_pass_through_16` - the 16-byte SAT block for one PIO data-in command
    * :data:`ATA_PASS_THROUGH_16` - its operation code

System Role:
    Adapter layer, shared by the Linux and Windows readers. No I/O.
"""

from __future__ import annotations

__all__ = [
    "ATA_IDENTIFY_DEVICE",
    "ATA_PASS_THROUGH_16",
    "ATA_SMART",
    "SMART_LBA_SIGNATURE",
    "SMART_READ_DATA",
    "SMART_READ_THRESHOLDS",
    "ata_pass_through_16",
]

#: ATA IDENTIFY DEVICE, which returns the drive's own identity sector.
ATA_IDENTIFY_DEVICE = 0xEC
#: The ATA SMART command; the features register picks the subcommand.
ATA_SMART = 0xB0
#: SMART READ DATA, the attribute table.
SMART_READ_DATA = 0xD0
#: SMART READ THRESHOLDS, the drive's own failure thresholds.
SMART_READ_THRESHOLDS = 0xD1
#: The fixed LBA-mid and LBA-high values every SMART command must carry.
SMART_LBA_SIGNATURE = 0xC24F00

#: The SAT operation code for the 16-byte form. It is the form a USB SATA bridge
#: was measured answering, and the one both readers send, although every command
#: lsdsk issues is a 28-bit one that the 12-byte form could also carry.
ATA_PASS_THROUGH_16 = 0x85

#: SAT's PROTOCOL field value for a PIO data-in command, shifted into bits 1-4.
#: Bit 0, EXTEND, stays clear: the command is a 28-bit one, so the translator
#: ignores the high half of every register pair.
_PIO_DATA_IN = 4 << 1

#: The widest LBA a 28-bit command carries: 24 bits in LBA low/mid/high, and
#: bits 27:24 in the low nibble of the DEVICE register.
_LBA_28_LIMIT = 1 << 28

#: The widest value an 8-bit register carries, which is what the features and
#: sector count registers are in a 28-bit command.
_BYTE_LIMIT = 1 << 8

#: T_DIR=1 (from the device), BYT_BLOK=1 (count in blocks), T_LENGTH=2 (the
#: length is in the sector count field): transfer ``count`` sectors to the host.
_SECTORS_FROM_THE_DEVICE = 0x0E


def ata_pass_through_16(*, command: int, feature: int = 0, lba: int = 0, count: int = 1) -> bytes:
    """Build the ATA PASS-THROUGH(16) block for one read-only 28-bit PIO data-in command.

    EXTEND is clear, so the translator issues a 28-bit command and ignores the
    high byte of every register pair; nothing is written there, and a value that
    would need one is refused rather than silently truncated.

    Args:
        command: The ATA command code.
        feature: The features register, one byte.
        lba: The LBA registers, which SMART uses as a fixed signature. At most
            28 bits: bits 27:24 go in the low nibble of the DEVICE register.
        count: How many 512-byte sectors the command returns, one byte.

    Returns:
        The 16 bytes of the command block. The upper nibble of the DEVICE
        register is left at zero, which SAT translators accept for these
        commands.

    Raises:
        ValueError: If ``lba``, ``feature`` or ``count`` does not fit the 28-bit
            command's registers.

    Example:
        >>> ata_pass_through_16(command=ATA_IDENTIFY_DEVICE).hex()
        '85080e0000000100000000000000ec00'
        >>> smart = ata_pass_through_16(command=ATA_SMART, feature=SMART_READ_DATA, lba=SMART_LBA_SIGNATURE)
        >>> smart[4], smart[10], smart[12], smart[14]
        (208, 79, 194, 176)
    """
    _require_within(name="lba", value=lba, limit=_LBA_28_LIMIT)
    _require_within(name="feature", value=feature, limit=_BYTE_LIMIT)
    _require_within(name="count", value=count, limit=_BYTE_LIMIT)
    block = bytearray(16)
    block[0] = ATA_PASS_THROUGH_16
    block[1] = _PIO_DATA_IN
    block[2] = _SECTORS_FROM_THE_DEVICE
    # Each register's EXTEND half (bytes 3, 5, 7, 9, 11) precedes its low half and
    # stays zero, which is why the low bytes land at 4, 6, 8, 10 and 12 rather
    # than side by side.
    block[4] = feature
    block[6] = count
    block[8] = lba & 0xFF
    block[10] = (lba >> 8) & 0xFF
    block[12] = (lba >> 16) & 0xFF
    block[13] = (lba >> 24) & 0x0F
    block[14] = command
    return bytes(block)


def _require_within(*, name: str, value: int, limit: int) -> None:
    """Refuse a register value the 28-bit command has no room for.

    Args:
        name: The argument's name, which the message starts with.
        value: The value asked for.
        limit: One past the largest value the register carries.

    Raises:
        ValueError: If ``value`` is negative or not below ``limit``.
    """
    if not 0 <= value < limit:
        msg = f"{name} {value:#x} does not fit a 28-bit ATA command (limit {limit - 1:#x})"
        raise ValueError(msg)
