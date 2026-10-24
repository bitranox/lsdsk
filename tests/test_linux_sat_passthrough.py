"""The Linux reader believes a SAT answer only when the drive moved the whole sector.

SG_IO reports a transport failure in ``host_status``/``driver_status``, the SCSI
verdict in ``status`` and the bytes it did NOT move in ``resid``. A translator
that answers without moving data, or that refuses the command with CHECK
CONDITION, leaves the host and driver fields clean, so a reader checking only
those stores the untouched, zero-filled buffer as a real IDENTIFY or SMART sector.
The Windows reader holds the same line through ``DataTransferLength``; these drive
the Linux one through a fake ``fcntl``, the edge it talks to, on every runner.
"""

from __future__ import annotations

import ctypes
import sys
import types

import pytest

from lsdsk.adapters.hw.linux import reader

#: A sector the drive answered, recognisable so a zero-filled buffer cannot pass as it.
SECTOR = bytes(range(256)) * 2


def _fake_fcntl(
    *, moved: int = len(SECTOR), status: int = 0, sense: bytes = b"", host: int = 0, driver: int = 0
) -> types.SimpleNamespace:
    """An ``fcntl`` whose SG_IO moves `moved` bytes of `SECTOR` and reports the rest as given.

    `sense` is written to the sense buffer with its length in ``sb_len_wr``, as the
    kernel does; `host` and `driver` land in ``host_status`` and ``driver_status``.
    """

    def ioctl(_fd: int, _request: int, header: reader.SgIoHeader) -> int:
        ctypes.memmove(header.dxferp, SECTOR, moved)
        header.resid = header.dxfer_len - moved
        header.status = status
        header.masked_status = status >> 1
        ctypes.memmove(header.sbp, sense, len(sense))
        header.sb_len_wr = len(sense)
        header.host_status = host
        header.driver_status = driver
        return 0

    # `import fcntl` hands back whatever sys.modules holds, so a namespace carrying
    # the one function the reader calls is a complete stand-in.
    return types.SimpleNamespace(ioctl=ioctl)


def _identify(monkeypatch: pytest.MonkeyPatch, fake: types.SimpleNamespace) -> bytes:
    monkeypatch.setitem(sys.modules, "fcntl", fake)
    return reader.ata_passthrough(-1, command=0xEC)


@pytest.mark.os_agnostic
def test_a_whole_sector_with_good_status_is_the_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: a clean answer is returned byte for byte."""
    assert _identify(monkeypatch, _fake_fcntl()) == SECTOR


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("moved", "named"),
    [pytest.param(0, "0 of 512", id="no data"), pytest.param(100, "100 of 512", id="a short transfer")],
)
def test_a_transfer_short_of_a_sector_is_refused(monkeypatch: pytest.MonkeyPatch, moved: int, named: str) -> None:
    with pytest.raises(OSError, match=named):
        _identify(monkeypatch, _fake_fcntl(moved=moved))


@pytest.mark.os_agnostic
def test_a_check_condition_is_refused_even_with_a_full_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHECK CONDITION says the command failed, whatever landed in the buffer."""
    with pytest.raises(OSError, match="SCSI status 0x02"):
        _identify(monkeypatch, _fake_fcntl(status=0x02))


#: What a QEMU scsi-hd answered for ATA PASS-THROUGH in the linux-ext4-xfs-filesystems
#: capture: CHECK CONDITION with fixed-format sense, ILLEGAL REQUEST, INVALID COMMAND
#: OPERATION CODE. The kernel sets DRIVER_SENSE (8) in driver_status because sense
#: data came back, which says the DEVICE answered, not that the transport failed.
_REFUSAL_SENSE = bytes([0x70, 0, 0x05, 0, 0, 0, 0, 10, 0, 0, 0, 0, 0x20, 0x00])
_DRIVER_SENSE = 0x08


@pytest.mark.os_agnostic
def test_a_refusal_carrying_sense_data_is_named_as_the_device_s_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """DRIVER_SENSE is the device answering, so the message names the status and the sense key."""
    fake = _fake_fcntl(moved=0, status=0x02, sense=_REFUSAL_SENSE, driver=_DRIVER_SENSE)

    with pytest.raises(OSError, match="SCSI status 0x02") as refused:
        _identify(monkeypatch, fake)

    message = str(refused.value)
    assert "transport" not in message
    assert message.endswith("sense key 0x5 ILLEGAL REQUEST, ASC/ASCQ 0x20/0x00")


@pytest.mark.os_agnostic
def test_a_check_condition_without_sense_data_names_the_status_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no sense bytes written there is no key to name, and none is invented from the zero fill."""
    with pytest.raises(OSError, match=r"^SG_IO SCSI status 0x02$"):
        _identify(monkeypatch, _fake_fcntl(status=0x02, driver=_DRIVER_SENSE))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("host", "driver"),
    [
        pytest.param(0x01, 0, id="host: no connect"),
        pytest.param(0, 0x06, id="driver: timeout"),
        pytest.param(0, 0x06 | _DRIVER_SENSE, id="driver: timeout with sense"),
    ],
)
def test_a_real_transport_failure_is_still_named_one(monkeypatch: pytest.MonkeyPatch, host: int, driver: int) -> None:
    """Only DRIVER_SENSE is the device's answer; every other host or driver code is the transport's."""
    with pytest.raises(OSError, match="SG_IO transport failure"):
        _identify(monkeypatch, _fake_fcntl(host=host, driver=driver))


def test_sense_cut_short_by_the_device_names_no_asc_from_the_zero_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the ``sb_len_wr`` bytes the device wrote are sense; the rest of the buffer is the reader's zero fill.

    Eight bytes of fixed-format sense carry the key but end before the ASC at
    offset 12, so there is no ASC/ASCQ to name. Read past the reported length,
    the zero fill would supply one: ``ASC/ASCQ 0x00/0x00``, a code the device
    never sent.
    """
    fake = _fake_fcntl(status=0x02, sense=_REFUSAL_SENSE[:8], driver=_DRIVER_SENSE)
    with pytest.raises(OSError, match=r"^SG_IO SCSI status 0x02$"):
        _identify(monkeypatch, fake)
