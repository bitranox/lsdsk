"""Decode what USB devices and Windows USB hubs say about link speed.

A device declares what it can do in its Binary device Object Store (BOS): the
SuperSpeed capability lists the speeds it supports, and the SuperSpeedPlus
capability lists each sublink's lane speed. The kernel publishes the same bytes
in sysfs, Windows hands them back through the hub, and a usbfs control request
fetches them on an older kernel, so one parser serves all three transports.

A BOS states lane SPEEDS and not a maximum lane COUNT, so a dual-lane device
reads as one lane here; the builder takes the running link as a lower bound on
what the device can do, which restores it whenever the link actually trained
both lanes.

The Windows half builds the exact request each hub IOCTL accepts and decodes
its answer. The sizes are measured: a request one byte short is refused with
ERROR_INVALID_PARAMETER.

References: USB 3.2 specification, section 9.6.2 (BOS and device capability
descriptors); usbioctl.h and usbspec.h in the Windows SDK.

System Role:
    Pure adapter-layer decoding.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ....domain.enums import UsbLaneRate
from ....domain.models import UsbSpeed

_BOS_DESCRIPTOR = 0x0F
_DEVICE_CAPABILITY = 0x10
_BOS_HEADER_LENGTH = 5
_CAPABILITY_HEADER_LENGTH = 3
_CAP_SUPERSPEED = 0x03
_CAP_CONTAINER_ID = 0x04
_CAP_SUPERSPEEDPLUS = 0x0A
_SUPERSPEED_LENGTH = 6
_CONTAINER_ID_START = 4
_CONTAINER_ID_LENGTH = 20
_SUPERSPEEDPLUS_HEADER = 12
_SUBLINK_ATTRIBUTE_SIZE = 4
_SPEEDS_5G = 0x08
_SUBLINK_COUNT_MASK = 0x1F

# A sublink speed attribute: the 16-bit Lane Speed Mantissa in the top half, and
# the Lane Speed Exponent in bits 5:4 naming the unit the mantissa counts in.
_MANTISSA_SHIFT = 16
_EXPONENT_SHIFT = 4
_EXPONENT_MASK = 0x3
_LANE_SPEED_UNIT_GBPS = {0: 1e-9, 1: 1e-6, 2: 1e-3, 3: 1.0}
_LANE_RATES_BY_GBPS = {5.0: UsbLaneRate.GEN1, 10.0: UsbLaneRate.GEN2}

# The kernel's `speed` attribute, in Mb/s, to the lane rate that produces it.
_SYSFS_LANE_RATES = {
    "1.5": UsbLaneRate.LOW,
    "12": UsbLaneRate.FULL,
    "480": UsbLaneRate.HIGH,
    "5000": UsbLaneRate.GEN1,
    "10000": UsbLaneRate.GEN2,
    "20000": UsbLaneRate.GEN2,
}
_SYSFS_TWO_LANE_TOTAL = "20000"
_SYSFS_AMBIGUOUS_TOTAL = "10000"
_TWO_LANES = 2

# USB_DEVICE_SPEED from usbspec.h as USB_NODE_CONNECTION_INFORMATION_EX reports it.
# It has nothing above SuperSpeed, which is why the V2 flags decide SuperSpeedPlus.
_CONNECTION_SPEEDS = {0: UsbLaneRate.LOW, 1: UsbLaneRate.FULL, 2: UsbLaneRate.HIGH, 3: UsbLaneRate.GEN1}
_CONNECTION_LENGTH = 35
_CONNECTION_SPEED_OFFSET = 23
_CONNECTION_HUB_OFFSET = 24
_CONNECTION_STATUS_OFFSET = 31
_DEVICE_CONNECTED = 1
_V2_LENGTH = 16
_V2_ALL_PROTOCOLS = 0x7
_PROTOCOL_USB300 = 0x4
_FLAG_OPERATING_SS = 0x1
_FLAG_SS_CAPABLE = 0x2
_FLAG_OPERATING_SSP = 0x4
_FLAG_SSP_CAPABLE = 0x8
_CONNECTOR_LENGTH = 18
_CONNECTOR_MINIMUM = 16
_PORT_TYPE_C = 0x8
_SUPERSPEEDPLUS_LENGTH = 24
_DESCRIPTOR_REQUEST_LENGTH = 12
_GET_DESCRIPTOR = 6
_DEVICE_TO_HOST = 0x80
_DESCRIPTOR_TYPE_SHIFT = 8
_HUB_TYPE_LENGTH = 4


@dataclass(frozen=True, slots=True)
class BosCapabilities:
    """What a device's BOS says about its speed, and the identifier a fixture scrub must find.

    Attributes:
        superspeed: Whether it declares SuperSpeed at all.
        fastest: The fastest single-lane speed it declares, or None for a USB 2 device.
        container_id: The 16-byte Container ID, unique to the device, when present.
    """

    superspeed: bool
    fastest: UsbSpeed | None
    container_id: bytes | None


@dataclass(frozen=True, slots=True)
class UsbConnection:
    """What `USB_NODE_CONNECTION_INFORMATION_EX` says about one hub port.

    Attributes:
        speed: The speed it reports; never above SuperSpeed (Gen 1).
        connected: Whether a device is connected.
        is_hub: Whether that device is a hub.
    """

    speed: UsbSpeed | None
    connected: bool
    is_hub: bool


@dataclass(frozen=True, slots=True)
class UsbProtocols:
    """What `USB_NODE_CONNECTION_INFORMATION_EX_V2` says about a port and the device in it.

    Attributes:
        port_usb3: The port speaks USB 3.
        operating_superspeed: The device runs at SuperSpeed or faster.
        superspeed_capable: The device can run at SuperSpeed or faster.
        operating_superspeedplus: The device runs at SuperSpeedPlus or faster.
        superspeedplus_capable: The device can run at SuperSpeedPlus or faster.
    """

    port_usb3: bool
    operating_superspeed: bool
    superspeed_capable: bool
    operating_superspeedplus: bool
    superspeedplus_capable: bool


@dataclass(frozen=True, slots=True)
class UsbConnector:
    """What `USB_PORT_CONNECTOR_PROPERTIES` says about a port's physical socket.

    Attributes:
        companion_port: The port number of the other-speed half of the same socket, 0 for none.
        type_c: The socket is USB-C.
    """

    companion_port: int
    type_c: bool


def fastest(*speeds: UsbSpeed | None) -> UsbSpeed | None:
    """Return the fastest of the speeds that were read.

    Args:
        *speeds: Speeds, any of which may be unread.

    Returns:
        The one with the most bandwidth, or None when none was read.

    Example:
        >>> fastest(None, UsbSpeed(lane_rate=UsbLaneRate.GEN1)).figure
        'USB5G'
    """
    known = [speed for speed in speeds if speed is not None]
    return max(known, key=lambda speed: speed.bandwidth_gbps) if known else None


def decode_bos(blob: bytes) -> BosCapabilities:
    """Decode a Binary device Object Store into the speeds it declares.

    Every length is checked against the descriptor before it is followed, so a
    record that claims more than the buffer holds is refused rather than read
    past, and a zero length cannot loop.

    Args:
        blob: The BOS descriptor followed by its device capability descriptors.

    Returns:
        What the descriptor declares.

    Raises:
        ValueError: The bytes are not a well-formed BOS.

    Example:
        >>> decode_bos(bytes.fromhex("050f0f00010a100300080001000000")).fastest.figure
        'USB5G'
    """
    total = _bos_total(blob)
    superspeed, speeds, container = False, list[UsbSpeed](), None
    offset = blob[0]
    for _ in range(blob[_BOS_HEADER_LENGTH - 1]):
        record = _capability_at(blob, offset, total)
        kind = record[2]
        if kind == _CAP_SUPERSPEED and _declares_5g(record):
            superspeed = True
            speeds.append(UsbSpeed(lane_rate=UsbLaneRate.GEN1))
        elif kind == _CAP_SUPERSPEEDPLUS:
            superspeed = True
            speeds.extend(_sublink_speeds(record))
        elif kind == _CAP_CONTAINER_ID and len(record) >= _CONTAINER_ID_LENGTH:
            container = bytes(record[_CONTAINER_ID_START:_CONTAINER_ID_LENGTH])
        offset += len(record)
    return BosCapabilities(superspeed=superspeed, fastest=fastest(*speeds), container_id=container)


def _bos_total(blob: bytes) -> int:
    """The total length a BOS header declares, refusing a header that is not one or overstates it."""
    if len(blob) < _BOS_HEADER_LENGTH:
        raise ValueError("BOS descriptor too short")
    if blob[1] != _BOS_DESCRIPTOR or blob[0] < _BOS_HEADER_LENGTH:
        raise ValueError("not a BOS descriptor")
    total = int.from_bytes(blob[2:4], "little")
    if total > len(blob):
        raise ValueError("BOS descriptor runs past the bytes read")
    return total


def _capability_at(blob: bytes, offset: int, total: int) -> bytes:
    """Return the device capability record at an offset, refusing one that does not fit."""
    if offset + _CAPABILITY_HEADER_LENGTH > total:
        raise ValueError("BOS capability header runs past the descriptor")
    length = blob[offset]
    if length < _CAPABILITY_HEADER_LENGTH or offset + length > total:
        raise ValueError(f"BOS capability length {length} does not fit the descriptor")
    if blob[offset + 1] != _DEVICE_CAPABILITY:
        raise ValueError("BOS capability length fits but the record is not a device capability")
    return blob[offset : offset + length]


def _declares_5g(record: bytes) -> bool:
    """Whether a SuperSpeed capability lists 5 Gb/s among the speeds it supports."""
    return len(record) >= _SUPERSPEED_LENGTH and bool(int.from_bytes(record[4:6], "little") & _SPEEDS_5G)


def _sublink_speeds(record: bytes) -> list[UsbSpeed]:
    """Every sublink lane speed a SuperSpeedPlus capability lists, as single-lane speeds."""
    if len(record) < _SUPERSPEEDPLUS_HEADER:
        raise ValueError("SuperSpeedPlus capability shorter than its header")
    count = (int.from_bytes(record[4:8], "little") & _SUBLINK_COUNT_MASK) + 1
    if len(record) < _SUPERSPEEDPLUS_HEADER + _SUBLINK_ATTRIBUTE_SIZE * count:
        raise ValueError(f"SuperSpeedPlus capability lists {count} sublink attributes it does not hold")
    attributes = struct.unpack_from(f"<{count}I", record, _SUPERSPEEDPLUS_HEADER)
    return [speed for attribute in attributes if (speed := _lane_speed(attribute, lanes=1)) is not None]


def _lane_speed(attribute: int, *, lanes: int) -> UsbSpeed | None:
    """Decode a sublink speed attribute (BOS and Windows share the layout) into a speed."""
    unit = _LANE_SPEED_UNIT_GBPS[(attribute >> _EXPONENT_SHIFT) & _EXPONENT_MASK]
    rate = _LANE_RATES_BY_GBPS.get(round((attribute >> _MANTISSA_SHIFT) * unit, 3))
    if rate is None or not 1 <= lanes <= _TWO_LANES:
        return None
    return UsbSpeed(lane_rate=rate, lanes=lanes)


def speed_from_sysfs(speed: str | None, rx_lanes: str | None, tx_lanes: str | None) -> UsbSpeed | None:
    """Decode the kernel's `speed` attribute and lane counts into a speed.

    The kernel reports the total: 10000 is one Gen 2 lane or two Gen 1 lanes,
    told apart by the lane count, and 20000 is two Gen 2 lanes.

    Args:
        speed: The `speed` attribute, in Mb/s.
        rx_lanes: The `rx_lanes` attribute.
        tx_lanes: The `tx_lanes` attribute.

    Returns:
        The speed, or None for a value the kernel does not publish.

    Example:
        >>> speed_from_sysfs("10000", "2", "2").bandwidth_gbps
        1.0
    """
    text = (speed or "").strip()
    rate = _SYSFS_LANE_RATES.get(text)
    if rate is None:
        return None
    two_lanes = text == _SYSFS_TWO_LANE_TOTAL or _lane_count(rx_lanes, tx_lanes) == _TWO_LANES
    if text == _SYSFS_AMBIGUOUS_TOTAL and two_lanes:
        rate = UsbLaneRate.GEN1
    return UsbSpeed(lane_rate=rate, lanes=_TWO_LANES if two_lanes else 1)


def _lane_count(rx_lanes: str | None, tx_lanes: str | None) -> int:
    """The number of lanes both directions run, one when either is unread."""
    try:
        return min(int((rx_lanes or "").strip()), int((tx_lanes or "").strip()))
    except ValueError:
        return 1


def connection_request(port: int) -> bytes:
    """Return the input `IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX` takes for a port.

    Args:
        port: The 1-based port number on the hub.

    Returns:
        The request bytes.
    """
    return struct.pack("<I", port)


def connection_v2_request(port: int) -> bytes:
    """Return the input `..._INFORMATION_EX_V2` takes: its own length and every protocol understood.

    Args:
        port: The 1-based port number on the hub.

    Returns:
        The request bytes.
    """
    return struct.pack("<IIII", port, _V2_LENGTH, _V2_ALL_PROTOCOLS, 0)


def connector_request(port: int) -> bytes:
    """Return the input `IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES` takes for a port.

    Args:
        port: The 1-based port number on the hub.

    Returns:
        The request bytes, including the one-character name tail the structure declares.
    """
    return struct.pack("<IIIHHH", port, _CONNECTOR_LENGTH, 0, 0, 0, 0)


def superspeedplus_request(port: int) -> bytes:
    """Return the input `IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION` takes.

    Args:
        port: The 1-based port number on the hub.

    Returns:
        The request bytes.
    """
    return struct.pack("<IIIIII", port, _SUPERSPEEDPLUS_LENGTH, 0, 0, 0, 0)


def bos_request(port: int, length: int) -> bytes:
    """Return a `USB_DESCRIPTOR_REQUEST` asking the device on a port for its BOS.

    Args:
        port: The 1-based port number on the hub.
        length: How many bytes of the BOS to ask for.

    Returns:
        The request header; the hub writes the descriptor after it.
    """
    value = _BOS_DESCRIPTOR << _DESCRIPTOR_TYPE_SHIFT
    return struct.pack("<IBBHHH", port, _DEVICE_TO_HOST, _GET_DESCRIPTOR, value, 0, length)


def descriptor_payload(buffer: bytes) -> bytes:
    """Return the descriptor a `USB_DESCRIPTOR_REQUEST` answer carries after its header.

    Args:
        buffer: What the hub returned.

    Returns:
        The descriptor bytes.
    """
    return buffer[_DESCRIPTOR_REQUEST_LENGTH:]


def decode_connection(buffer: bytes) -> UsbConnection:
    """Decode `USB_NODE_CONNECTION_INFORMATION_EX` (packed, so the speed sits at byte 23).

    Args:
        buffer: What the hub returned.

    Returns:
        The port's connection.

    Raises:
        ValueError: The buffer is shorter than the structure.
    """
    if len(buffer) < _CONNECTION_LENGTH:
        raise ValueError(f"connection information short: {len(buffer)} of {_CONNECTION_LENGTH} bytes")
    rate = _CONNECTION_SPEEDS.get(buffer[_CONNECTION_SPEED_OFFSET])
    status = int.from_bytes(buffer[_CONNECTION_STATUS_OFFSET : _CONNECTION_STATUS_OFFSET + 4], "little")
    return UsbConnection(
        speed=None if rate is None else UsbSpeed(lane_rate=rate),
        connected=status == _DEVICE_CONNECTED,
        is_hub=bool(buffer[_CONNECTION_HUB_OFFSET]),
    )


def decode_connection_v2(buffer: bytes) -> UsbProtocols:
    """Decode `USB_NODE_CONNECTION_INFORMATION_EX_V2`: what the port speaks and what the device does.

    Args:
        buffer: What the hub returned.

    Returns:
        The protocols and flags.

    Raises:
        ValueError: The buffer is shorter than the structure.
    """
    if len(buffer) < _V2_LENGTH:
        raise ValueError(f"connection information V2 short: {len(buffer)} of {_V2_LENGTH} bytes")
    _port, _length, protocols, flags = struct.unpack_from("<IIII", buffer)
    return UsbProtocols(
        port_usb3=bool(protocols & _PROTOCOL_USB300),
        operating_superspeed=bool(flags & _FLAG_OPERATING_SS),
        superspeed_capable=bool(flags & _FLAG_SS_CAPABLE),
        operating_superspeedplus=bool(flags & _FLAG_OPERATING_SSP),
        superspeedplus_capable=bool(flags & _FLAG_SSP_CAPABLE),
    )


def decode_connector(buffer: bytes) -> UsbConnector:
    """Decode `USB_PORT_CONNECTOR_PROPERTIES`: the companion port and the connector type.

    Args:
        buffer: What the hub returned.

    Returns:
        The connector.

    Raises:
        ValueError: The buffer is shorter than the fixed part of the structure.
    """
    if len(buffer) < _CONNECTOR_MINIMUM:
        raise ValueError(f"connector properties short: {len(buffer)} of {_CONNECTOR_MINIMUM} bytes")
    _port, _length, properties, _companion_index, companion_port = struct.unpack_from("<IIIHH", buffer)
    return UsbConnector(companion_port=companion_port, type_c=bool(properties & _PORT_TYPE_C))


def decode_superspeedplus(buffer: bytes) -> UsbSpeed | None:
    """Decode `USB_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION` into the receive lane rate and count.

    Args:
        buffer: What the hub returned.

    Returns:
        The speed, or None for a lane rate this tool does not know.

    Raises:
        ValueError: The buffer is shorter than the structure.
    """
    if len(buffer) < _SUPERSPEEDPLUS_LENGTH:
        raise ValueError(f"SuperSpeedPlus information short: {len(buffer)} of {_SUPERSPEEDPLUS_LENGTH} bytes")
    _port, _length, receive, receive_lanes, _transmit, _transmit_lanes = struct.unpack_from("<IIIIII", buffer)
    return _lane_speed(receive, lanes=receive_lanes)


def decode_hub_type(buffer: bytes) -> int:
    """Return the `USB_HUB_TYPE` of a `USB_HUB_INFORMATION_EX`: 1 root, 2 USB 2, 3 USB 3.

    Args:
        buffer: What the hub returned.

    Returns:
        The hub type.

    Raises:
        ValueError: The buffer is shorter than the type field.
    """
    if len(buffer) < _HUB_TYPE_LENGTH:
        raise ValueError("hub information short")
    return int.from_bytes(buffer[:_HUB_TYPE_LENGTH], "little")


__all__ = [
    "BosCapabilities",
    "UsbConnection",
    "UsbConnector",
    "UsbProtocols",
    "bos_request",
    "connection_request",
    "connection_v2_request",
    "connector_request",
    "decode_bos",
    "decode_connection",
    "decode_connection_v2",
    "decode_connector",
    "decode_hub_type",
    "decode_superspeedplus",
    "descriptor_payload",
    "fastest",
    "speed_from_sysfs",
    "superspeedplus_request",
]
