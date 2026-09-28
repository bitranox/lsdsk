"""A drive behind a USB bridge or a SAS adapter is asked its ATA questions through SAT.

Windows' storage stack refuses ``IOCTL_ATA_PASS_THROUGH`` for a SATA drive it
reaches as a SCSI device - measured, a USB SATA SSD refused IDENTIFY and both
SMART reads with Win32 error 1, so the machine's one external drive reported no
counters at all. The same ATA command wrapped in SAT's ATA PASS-THROUGH reaches it
through the bridge's translator. These tests drive the Windows reader through a
fake ``kernel32``, which is the edge it talks to, so the fallback is held on every
runner rather than only on the one machine that owns such a drive.
"""

from __future__ import annotations

import base64
import ctypes
from typing import TYPE_CHECKING, cast

import pytest

from lsdsk.adapters.hw import ata_commands as ata
from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api

if TYPE_CHECKING:
    from collections.abc import Callable

#: The command blocks a real USB SATA bridge answered on Windows, as bytes, so a
#: change to the builder that a translator would reject cannot pass as a refactor.
MEASURED_IDENTIFY = bytes.fromhex("85080e0000000100000000000000ec00")
MEASURED_SMART_READ_DATA = bytes.fromhex("85080e00d000010000004f00c200b000")


@pytest.mark.os_agnostic
def test_the_command_blocks_are_the_ones_a_real_bridge_answered() -> None:
    assert ata.ata_pass_through_16(command=ata.ATA_IDENTIFY_DEVICE) == MEASURED_IDENTIFY
    assert (
        ata.ata_pass_through_16(command=ata.ATA_SMART, feature=ata.SMART_READ_DATA, lba=ata.SMART_LBA_SIGNATURE)
        == MEASURED_SMART_READ_DATA
    )


@pytest.mark.os_agnostic
def test_a_28_bit_lba_puts_its_top_nibble_in_the_device_byte_and_writes_no_extend_byte() -> None:
    """The block is a 28-bit command, so LBA 27:24 belongs in DEVICE and nothing in the EXTEND half.

    With EXTEND clear a translator issues a 28-bit command and ignores bytes 3, 5,
    7, 9 and 11, so a value written there is silently dropped and LBA 27:24 has
    to travel in the low nibble of the DEVICE byte instead.
    """
    block = ata.ata_pass_through_16(command=0x20, feature=0xAB, lba=0x0A0B0C0D, count=0xFF)

    assert block[1] & 0x01 == 0, "EXTEND is set, so this is no longer the 28-bit command it is documented as"
    assert (block[4], block[6]) == (0xAB, 0xFF)
    assert (block[8], block[10], block[12]) == (0x0D, 0x0C, 0x0B)
    assert block[13] == 0x0A
    assert (block[3], block[5], block[7], block[9], block[11]) == (0, 0, 0, 0, 0)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        pytest.param({"lba": 1 << 28}, "lba", id="an LBA past 28 bits"),
        pytest.param({"count": 256}, "count", id="a count past one byte"),
        pytest.param({"feature": 0x100}, "feature", id="a feature past one byte"),
        pytest.param({"lba": -1}, "lba", id="a negative LBA"),
    ],
)
def test_a_register_value_a_28_bit_command_cannot_carry_is_refused(arguments: dict[str, int], named: str) -> None:
    with pytest.raises(ValueError, match=rf"^{named} "):
        ata.ata_pass_through_16(command=0x20, **arguments)


#: What the fake ATA ioctl returns when it answers in full.
ATA_SECTOR = b"\x11" * 512


class FakeKernel32:
    """``kernel32`` as the reader sees it: one ``DeviceIoControl`` per request.

    The ATA ioctl answers per ``ata_answers`` with ``ata_sector``; the SCSI
    passthrough answers per ``sat_answers``, keyed by the ATA command inside the
    SAT block, with the bytes moved, a SCSI status, or ``None`` for an ioctl
    failure. Whatever bytes it answers with, it writes their count back into
    ``DataTransferLength``, because that is what the kernel does: an empty or
    short answer is a transfer that moved less than the sector asked for.
    """

    def __init__(
        self,
        *,
        ata_answers: bool,
        sat_answers: Callable[[int], bytes | int | None],
        ata_sector: bytes = ATA_SECTOR,
    ) -> None:
        """Answer the ATA ioctl or refuse it, and the SAT one through ``sat_answers``."""
        self.ata_answers = ata_answers
        self.sat_answers = sat_answers
        self.ata_sector = ata_sector
        self.sat_blocks: list[bytes] = []

    def DeviceIoControl(  # noqa: N802 - the Win32 entry point's own name
        self, handle: int, code: int, in_buffer: object, *_rest: object
    ) -> int:
        del handle
        if code == api.IOCTL_ATA_PASS_THROUGH_DIRECT:
            request = cast("api.ATA_PASS_THROUGH_DIRECT", getattr(in_buffer, "_obj"))  # noqa: B009 - CArgObject
            if not self.ata_answers:
                return 0
            ctypes.memmove(request.DataBuffer, self.ata_sector, len(self.ata_sector))
            request.DataTransferLength = len(self.ata_sector)
            return 1
        if code == api.IOCTL_SCSI_PASS_THROUGH:
            holder = getattr(in_buffer, "_obj")  # noqa: B009 - CArgObject
            block = bytes(holder.request.Cdb[: holder.request.CdbLength])
            self.sat_blocks.append(block)
            answer = self.sat_answers(block[14])
            if answer is None:
                return 0
            if isinstance(answer, int):
                holder.request.ScsiStatus = answer
                return 1
            ctypes.memmove(holder.data, answer, len(answer))
            holder.request.DataTransferLength = len(answer)
            return 1
        pytest.fail(f"an ioctl the ATA read should not issue: 0x{code:08X}")


def _sector_for(command: int) -> bytes:
    return bytes([command]) * 512


def _read(fake: FakeKernel32) -> dict[str, str]:
    return reader.read_ata(cast("api.WinLibrary", fake), handle=1)


@pytest.mark.os_agnostic
def test_a_drive_the_ata_ioctl_cannot_reach_is_read_through_sat() -> None:
    fake = FakeKernel32(ata_answers=False, sat_answers=_sector_for)

    record = _read(fake)

    assert base64.b64decode(record["identify"]) == _sector_for(ata.ATA_IDENTIFY_DEVICE)
    assert base64.b64decode(record["smart_data"]) == _sector_for(ata.ATA_SMART)
    assert base64.b64decode(record["smart_thresholds"]) == _sector_for(ata.ATA_SMART)
    assert not any(key.endswith("_error") for key in record), record
    assert fake.sat_blocks[:2] == [MEASURED_IDENTIFY, MEASURED_SMART_READ_DATA]
    assert fake.sat_blocks[2][4] == ata.SMART_READ_THRESHOLDS


@pytest.mark.os_agnostic
def test_a_drive_the_ata_ioctl_reaches_is_never_asked_through_sat() -> None:
    fake = FakeKernel32(ata_answers=True, sat_answers=_sector_for)

    record = _read(fake)

    assert base64.b64decode(record["identify"]) == ATA_SECTOR
    assert fake.sat_blocks == [], "SAT was asked although the ATA ioctl answered"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("answer", "named"),
    [
        pytest.param(None, "SAT refused, Win32 error", id="the SCSI ioctl fails"),
        pytest.param(0x02, "SAT refused, SCSI status 0x02", id="the translator rejects the command"),
    ],
)
def test_a_reading_refused_both_ways_records_both_reasons(answer: int | None, named: str) -> None:
    fake = FakeKernel32(ata_answers=False, sat_answers=lambda _command: answer)

    record = _read(fake)

    assert set(record) == {"identify_error", "smart_data_error", "smart_thresholds_error"}, record
    for reason in record.values():
        assert reason.startswith("passthrough refused, Win32 error "), reason
        assert named in reason, reason


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("moved", "named"),
    [
        pytest.param(0, "SAT refused, 0 of 512 bytes returned", id="GOOD status with no data"),
        pytest.param(100, "SAT refused, 100 of 512 bytes returned", id="a short transfer"),
    ],
)
def test_a_translator_that_moves_less_than_a_sector_records_a_refusal_not_a_reading(moved: int, named: str) -> None:
    """A GOOD status is not a sector: the zeros left in the buffer are no drive's answer.

    Before this was checked, a translator answering GOOD with no data stored an
    all-zero sector as the drive's identity and SMART table, with no refusal, so
    the scan reported complete over readings nobody took.
    """
    fake = FakeKernel32(ata_answers=False, sat_answers=lambda command: _sector_for(command)[:moved])

    record = _read(fake)

    assert set(record) == {"identify_error", "smart_data_error", "smart_thresholds_error"}, record
    for reason in record.values():
        assert named in reason, reason


@pytest.mark.os_agnostic
@pytest.mark.parametrize("moved", [pytest.param(0, id="no data"), pytest.param(100, id="a short transfer")])
def test_an_ata_ioctl_that_moves_less_than_a_sector_falls_through_to_sat(moved: int) -> None:
    fake = FakeKernel32(ata_answers=True, sat_answers=_sector_for, ata_sector=ATA_SECTOR[:moved])

    record = _read(fake)

    assert base64.b64decode(record["identify"]) == _sector_for(ata.ATA_IDENTIFY_DEVICE)
    assert base64.b64decode(record["smart_data"]) == _sector_for(ata.ATA_SMART)
    assert not any(key.endswith("_error") for key in record), record
    assert len(fake.sat_blocks) == 3, "a short ATA transfer was kept instead of asking SAT"


@pytest.mark.os_agnostic
def test_a_short_transfer_both_ways_names_both_shortfalls() -> None:
    fake = FakeKernel32(ata_answers=True, sat_answers=lambda _command: b"", ata_sector=ATA_SECTOR[:100])

    record = _read(fake)

    assert set(record) == {"identify_error", "smart_data_error", "smart_thresholds_error"}, record
    for reason in record.values():
        assert reason == "passthrough refused, 100 of 512 bytes returned; SAT refused, 0 of 512 bytes returned", reason
