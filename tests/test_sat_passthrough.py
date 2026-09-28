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
def test_a_48_bit_lba_and_a_wide_count_land_in_their_extend_bytes() -> None:
    block = ata.ata_pass_through_16(command=0x25, feature=0x1234, lba=0x0A0B0C0D0E0F, count=0x0102)

    assert (block[3], block[4]) == (0x12, 0x34)
    assert (block[5], block[6]) == (0x01, 0x02)
    assert (block[7], block[9], block[11]) == (0x0C, 0x0B, 0x0A)
    assert (block[8], block[10], block[12]) == (0x0F, 0x0E, 0x0D)


class FakeKernel32:
    """``kernel32`` as the reader sees it: one ``DeviceIoControl`` per request.

    The ATA ioctl answers per ``ata_answers``; the SCSI passthrough answers per
    ``sat_answers``, keyed by the ATA command inside the SAT block, with a
    sector, a SCSI status, or ``None`` for an ioctl failure.
    """

    def __init__(self, *, ata_answers: bool, sat_answers: Callable[[int], bytes | int | None]) -> None:
        """Answer the ATA ioctl or refuse it, and the SAT one through ``sat_answers``."""
        self.ata_answers = ata_answers
        self.sat_answers = sat_answers
        self.sat_blocks: list[bytes] = []

    def DeviceIoControl(  # noqa: N802 - the Win32 entry point's own name
        self, handle: int, code: int, in_buffer: object, *_rest: object
    ) -> int:
        del handle
        if code == api.IOCTL_ATA_PASS_THROUGH_DIRECT:
            request = cast("api.ATA_PASS_THROUGH_DIRECT", getattr(in_buffer, "_obj"))  # noqa: B009 - CArgObject
            if not self.ata_answers:
                return 0
            ctypes.memmove(request.DataBuffer, b"\x11" * 512, 512)
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

    assert base64.b64decode(record["identify"]) == b"\x11" * 512
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
