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


def _fake_fcntl(*, moved: int = len(SECTOR), status: int = 0) -> types.SimpleNamespace:
    """An ``fcntl`` whose SG_IO writes `moved` bytes of `SECTOR` and reports `status`."""

    def ioctl(_fd: int, _request: int, header: reader.SgIoHeader) -> int:
        ctypes.memmove(header.dxferp, SECTOR, moved)
        header.resid = header.dxfer_len - moved
        header.status = status
        header.masked_status = status >> 1
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
