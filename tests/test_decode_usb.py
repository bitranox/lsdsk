"""USB descriptors and Windows hub buffers decode to the speeds they declare, and malformed input is refused."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.decode import usb
from lsdsk.domain.enums import UsbLaneRate
from lsdsk.domain.models import UsbSpeed

if TYPE_CHECKING:
    from collections.abc import Callable

# The BOS of a real 10 Gb/s UAS SSD (0781:558c), read through usbfs on Linux and
# through the hub on Windows, byte-identical on both.
SANDISK_BOS = bytes.fromhex("050f2a00030710021ef400000a1003000e00010aff0714100a00010000000011000030400a00b0400a00")
GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
GEN2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)


def _bos(*capabilities: bytes) -> bytes:
    body = b"".join(capabilities)
    return struct.pack("<BBHB", 5, 0x0F, 5 + len(body), len(capabilities)) + body


def _superspeed(speeds: int) -> bytes:
    return struct.pack("<BBBBHBBH", 10, 0x10, 0x03, 0, speeds, 1, 10, 0)


def _container(uuid: bytes) -> bytes:
    return struct.pack("<BBBB", 20, 0x10, 0x04, 0) + uuid


@pytest.mark.os_agnostic
def test_a_real_10g_device_decodes_as_superspeedplus_gen2() -> None:
    decoded = usb.decode_bos(SANDISK_BOS)
    assert decoded.superspeed
    assert decoded.fastest == GEN2
    assert decoded.container_id is None


@pytest.mark.os_agnostic
def test_a_superspeed_only_device_decodes_as_gen1() -> None:
    assert usb.decode_bos(_bos(_superspeed(0x000E))).fastest == GEN1


@pytest.mark.os_agnostic
def test_a_usb2_only_bos_declares_no_superspeed() -> None:
    usb2_extension = struct.pack("<BBBI", 7, 0x10, 0x02, 0x1E)
    decoded = usb.decode_bos(_bos(usb2_extension))
    assert not decoded.superspeed
    assert decoded.fastest is None


@pytest.mark.os_agnostic
def test_the_container_id_is_carried_so_a_fixture_scrub_can_find_it() -> None:
    uuid = bytes(range(16))
    assert usb.decode_bos(_bos(_container(uuid))).container_id == uuid


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("blob", "reason"),
    [
        (b"", "too short"),
        (bytes.fromhex("050e050000"), "not a BOS"),
        (_bos(_superspeed(0x8))[:-3], "runs past"),
        (struct.pack("<BBHB", 5, 0x0F, 8, 1) + bytes([0, 0x10, 0x03]), "capability length"),
        (struct.pack("<BBHB", 5, 0x0F, 5, 2), "runs past"),
        (_bos(struct.pack("<BBBBI", 12, 0x10, 0x0A, 0, 0x1F) + bytes(4)), "sublink"),
    ],
)
def test_a_malformed_bos_is_refused_rather_than_read_on(blob: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        usb.decode_bos(blob)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("speed", "rx", "tx", "expected"),
    [
        ("480", "1", "1", "USB480M"),
        ("5000", "1", "1", "USB5G"),
        ("10000", "1", "1", "USB10G"),
        ("10000", "2", "2", "USB10G"),
        ("20000", "2", "2", "USB20G"),
        ("1.5", None, None, "USB1.5M"),
    ],
)
def test_a_sysfs_speed_decodes_with_its_lanes(speed: str, rx: str | None, tx: str | None, expected: str) -> None:
    decoded = usb.speed_from_sysfs(speed, rx, tx)
    assert decoded is not None and decoded.figure == expected


@pytest.mark.os_agnostic
def test_two_gen1_lanes_are_priced_as_gen1_not_gen2() -> None:
    assert usb.speed_from_sysfs("10000", "2", "2") == UsbSpeed(lane_rate=UsbLaneRate.GEN1, lanes=2)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("speed", [None, "", "unknown", "999", "5000.5"])
def test_an_unrecognised_sysfs_speed_is_unread(speed: str | None) -> None:
    assert usb.speed_from_sysfs(speed, "1", "1") is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("build", "size"),
    [
        (usb.connection_request, 4),
        (usb.connection_v2_request, 16),
        (usb.connector_request, 18),
        (usb.superspeedplus_request, 24),
    ],
)
def test_every_hub_request_is_the_size_windows_accepts(build: Callable[[int], bytes], size: int) -> None:
    # Measured on Windows 11 26200: one byte short and the hub answers ERROR_INVALID_PARAMETER (87).
    request = build(3)
    assert len(request) == size
    assert struct.unpack_from("<I", request, 0)[0] == 3


@pytest.mark.os_agnostic
def test_the_v2_request_names_its_own_length_and_every_protocol() -> None:
    _port, length, protocols, _flags = struct.unpack("<IIII", usb.connection_v2_request(3))
    assert (length, protocols) == (16, 0x7)


@pytest.mark.os_agnostic
def test_the_bos_request_asks_for_the_bos_descriptor() -> None:
    port, request_type, request, value, index, length = struct.unpack("<IBBHHH", usb.bos_request(3, 42))
    assert (port, request_type, request, value >> 8, index, length) == (3, 0x80, 6, 0x0F, 0, 42)


def _connection(speed_code: int, *, hub: bool = False, status: int = 1) -> bytes:
    raw = bytearray(35)
    raw[23], raw[24] = speed_code, int(hub)
    struct.pack_into("<I", raw, 31, status)
    return bytes(raw)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("code", "figure"), [(0, "USB1.5M"), (1, "USB12M"), (2, "USB480M"), (3, "USB5G")])
def test_a_connection_speed_code_decodes(code: int, figure: str) -> None:
    decoded = usb.decode_connection(_connection(code))
    assert decoded.speed is not None and decoded.speed.figure == figure
    assert decoded.connected


@pytest.mark.os_agnostic
def test_a_connection_reports_a_hub_and_an_empty_port() -> None:
    decoded = usb.decode_connection(_connection(2, hub=True, status=0))
    assert decoded.is_hub
    assert not decoded.connected


@pytest.mark.os_agnostic
def test_a_short_connection_buffer_is_refused() -> None:
    with pytest.raises(ValueError, match="short"):
        usb.decode_connection(bytes(34))


@pytest.mark.os_agnostic
def test_the_v2_flags_of_a_real_usb2_port_holding_a_10g_disk() -> None:
    # Captured: a USB 2 hub port, the disk declares SuperSpeed and SuperSpeedPlus, runs at 480.
    decoded = usb.decode_connection_v2(bytes.fromhex("0300000010000000030000000a000000"))
    assert not decoded.port_usb3
    assert decoded.superspeed_capable and decoded.superspeedplus_capable
    assert not decoded.operating_superspeed and not decoded.operating_superspeedplus


@pytest.mark.os_agnostic
def test_the_connector_names_the_usb3_companion_of_a_usb2_root_port() -> None:
    raw = struct.pack("<IIIHH", 3, 18, 0x1, 0, 18) + b"\0\0"
    decoded = usb.decode_connector(raw)
    assert decoded.companion_port == 18 and not decoded.type_c


@pytest.mark.os_agnostic
def test_a_superspeedplus_answer_decodes_lane_rate_and_count() -> None:
    sublink_10g = (10 << 16) | (1 << 14) | (3 << 4)
    raw = struct.pack("<IIIIII", 17, 24, sublink_10g, 2, sublink_10g, 2)
    assert usb.decode_superspeedplus(raw) == UsbSpeed(lane_rate=UsbLaneRate.GEN2, lanes=2)


@pytest.mark.os_agnostic
def test_the_hub_type_and_the_descriptor_payload_decode() -> None:
    assert usb.decode_hub_type(bytes.fromhex("0200000004")) == 2
    assert usb.descriptor_payload(usb.bos_request(3, 5) + b"\x05\x0f") == b"\x05\x0f"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "decode",
    [usb.decode_connection_v2, usb.decode_connector, usb.decode_superspeedplus, usb.decode_hub_type],
)
def test_every_short_hub_answer_is_refused(decode: Callable[[bytes], object]) -> None:
    with pytest.raises(ValueError, match="short"):
        decode(b"\x00\x00\x00")


@pytest.mark.os_agnostic
def test_fastest_ignores_what_was_not_read() -> None:
    assert usb.fastest(None, GEN1, GEN2, None) == GEN2
    assert usb.fastest(None, None) is None
