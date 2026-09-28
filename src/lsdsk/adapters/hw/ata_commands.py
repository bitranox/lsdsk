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

#: The SAT operation code for the 16-byte form, which splits each register into
#: a high and a low byte so 48-bit addressing fits.
ATA_PASS_THROUGH_16 = 0x85

#: SAT's PROTOCOL field value for a PIO data-in command, shifted into bits 1-4.
_PIO_DATA_IN = 4 << 1

#: T_DIR=1 (from the device), BYT_BLOK=1 (count in blocks), T_LENGTH=2 (the
#: length is in the sector count field): transfer ``count`` sectors to the host.
_SECTORS_FROM_THE_DEVICE = 0x0E


def ata_pass_through_16(*, command: int, feature: int = 0, lba: int = 0, count: int = 1) -> bytes:
    """Build the ATA PASS-THROUGH(16) block for one read-only PIO data-in command.

    Args:
        command: The ATA command code.
        feature: The features register.
        lba: The LBA registers, which SMART uses as a fixed signature.
        count: How many 512-byte sectors the command returns.

    Returns:
        The 16 bytes of the command block. The device register is left at zero,
        which SAT translators accept for these commands.

    Example:
        >>> ata_pass_through_16(command=ATA_IDENTIFY_DEVICE).hex()
        '85080e0000000100000000000000ec00'
        >>> smart = ata_pass_through_16(command=ATA_SMART, feature=SMART_READ_DATA, lba=SMART_LBA_SIGNATURE)
        >>> smart[4], smart[10], smart[12], smart[14]
        (208, 79, 194, 176)
    """
    block = bytearray(16)
    block[0] = ATA_PASS_THROUGH_16
    block[1] = _PIO_DATA_IN
    block[2] = _SECTORS_FROM_THE_DEVICE
    block[3] = (feature >> 8) & 0xFF
    block[4] = feature & 0xFF
    block[5] = (count >> 8) & 0xFF
    block[6] = count & 0xFF
    # Each LBA byte's EXTEND half precedes its low half, which is why the three
    # low bytes land at 8, 10 and 12 rather than side by side.
    block[7] = (lba >> 24) & 0xFF
    block[8] = lba & 0xFF
    block[9] = (lba >> 32) & 0xFF
    block[10] = (lba >> 8) & 0xFF
    block[11] = (lba >> 40) & 0xFF
    block[12] = (lba >> 16) & 0xFF
    block[14] = command
    return bytes(block)
