"""A refusal names the sense key and ASC/ASCQ only where the sense data really carries them.

Both readers pass whatever sense bytes the transport reported, which can be
none, a layout this decoder does not know, or a buffer cut before the fields.
Each of those must describe nothing, never a key read from the wrong offset.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode.scsi_sense import describe_sense


def _fixed(code: int, key: int, asc: int, ascq: int) -> bytes:
    return bytes([code, 0, key, 0, 0, 0, 0, 10, 0, 0, 0, 0, asc, ascq])


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("sense", "described"),
    [
        pytest.param(_fixed(0x70, 0x05, 0x20, 0x00), "0x5 ILLEGAL REQUEST, ASC/ASCQ 0x20/0x00", id="fixed, current"),
        pytest.param(_fixed(0x71, 0x03, 0x11, 0x04), "0x3 MEDIUM ERROR, ASC/ASCQ 0x11/0x04", id="fixed, deferred"),
        pytest.param(_fixed(0xF0, 0x05, 0x24, 0x00), "0x5 ILLEGAL REQUEST, ASC/ASCQ 0x24/0x00", id="fixed, VALID bit"),
        pytest.param(bytes([0x72, 0x02, 0x04, 0x01]), "0x2 NOT READY, ASC/ASCQ 0x04/0x01", id="descriptor, current"),
        pytest.param(bytes([0x73, 0x0B, 0, 0]), "0xB ABORTED COMMAND, ASC/ASCQ 0x00/0x00", id="descriptor, deferred"),
        pytest.param(_fixed(0x70, 0xC5, 0x00, 0x00), "0x5 ILLEGAL REQUEST", id="flag bits above the key are ignored"),
        pytest.param(_fixed(0x70, 0x0C, 0x00, 0x00), "0xC RESERVED", id="a reserved key is named reserved"),
    ],
)
def test_a_sense_layout_is_read_at_its_own_offsets(sense: bytes, described: str) -> None:
    result = describe_sense(sense)

    assert result is not None
    assert result.startswith(f"sense key {described}")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "sense",
    [
        pytest.param(b"", id="nothing written"),
        pytest.param(bytes(32), id="the zero fill"),
        pytest.param(bytes([0x7F, 0x05, 0x20, 0x00]), id="a vendor response code"),
        pytest.param(_fixed(0x70, 0x05, 0x20, 0x00)[:13], id="fixed, cut before ASCQ"),
        pytest.param(bytes([0x72, 0x05, 0x20]), id="descriptor, cut before ASCQ"),
    ],
)
def test_sense_that_does_not_carry_the_fields_describes_nothing(sense: bytes) -> None:
    assert describe_sense(sense) is None
