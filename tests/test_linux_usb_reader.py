"""The Linux reader records a USB disk's chain to its root hub, and its BOS from sysfs or usbfs."""

from __future__ import annotations

import base64
import ctypes
import errno
import json
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.capture import MAX_DEVICE_TEXT
from lsdsk.adapters.hw.linux import reader, usbfs
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.snapshot import load
from lsdsk.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

BOS = bytes.fromhex("050f0f00010a100300080001000000")


def _device(path: Path, **attrs: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "idVendor").write_text("0781\n")
    for name, value in attrs.items():
        (path / name).write_text(value + "\n")
    return path


def _chain(root: Path) -> tuple[Path, Path, Path]:
    """usb1 (root, USB 2) -> 1-1 (hub) -> 1-1.2 (the disk), as on a USB2-only controller."""
    usb1 = _device(root / "pci0000:00" / "0000:00:1d.0" / "usb1", speed="480", version=" 2.00", bDeviceClass="09")
    hub = _device(usb1 / "1-1", speed="480", version=" 2.00", bDeviceClass="09")
    disk = _device(hub / "1-1.2", speed="480", version=" 2.10", bDeviceClass="00")
    (usb1 / "1-0:1.0" / "usb1-port1").mkdir(parents=True)
    (hub / "1-1:1.0" / "1-1-port2").mkdir(parents=True)
    interface = disk / "1-1.2:1.0"
    (interface / "host14").mkdir(parents=True)
    driver = root / "bus" / "usb" / "drivers" / "uas"
    driver.mkdir(parents=True)
    (interface / "driver").symlink_to(driver)
    return usb1, hub, disk


def _block(disk: Path) -> dict[str, dict[str, str]]:
    return {"sdb": {"device_path": str(disk / "1-1.2:1.0" / "host14" / "target14:0:0" / "14:0:0:0")}}


@pytest.mark.os_posix
def test_a_usb_disk_records_every_device_up_to_its_root_hub(tmp_path: Path) -> None:
    usb1, hub, disk = _chain(tmp_path)
    (disk / "bos_descriptors").write_bytes(BOS)
    found = reader.read_usb(_block(disk))
    assert set(found) == {str(usb1), str(hub), str(disk)}
    assert found[str(disk)]["interface_drivers"] == ["uas"]
    assert found[str(disk)]["speed"] == "480"
    assert base64.b64decode(found[str(disk)]["bos"]) == BOS


@pytest.mark.os_posix
def test_what_the_reader_records_is_a_capture_the_model_accepts(tmp_path: Path) -> None:
    _usb1, _hub, disk = _chain(tmp_path)
    usb = reader.read_usb(_block(disk))
    capture = LinuxCapture.model_validate(
        {"schema": 2, "platform": "linux", "hostname": "h", "kernel": "k", "pci": {}, "usb": usb}
    )
    assert capture.usb[str(disk)].device_class == "00"
    assert capture.usb[str(disk)].interface_drivers == ("uas",)


@pytest.mark.os_posix
def test_a_device_below_usb_2_01_has_no_bos_and_is_not_asked(tmp_path: Path) -> None:
    _usb1, hub, disk = _chain(tmp_path)
    found = reader.read_usb(_block(disk))
    assert found[str(hub)].get("bos_none") is True
    assert "bos_error" not in found[str(hub)]
    # control: a 2.10 device with no published BOS and no usbfs node to ask is unread, not "none"
    assert "bos_none" not in found[str(disk)]


@pytest.mark.os_posix
def test_a_port_with_a_usb3_twin_names_the_hub_that_owns_it(tmp_path: Path) -> None:
    usb1, hub, _disk = _chain(tmp_path)
    usb2 = _device(tmp_path / "pci0000:00" / "0000:00:1d.0" / "usb2", speed="5000", version=" 3.00", bDeviceClass="09")
    twin = usb2 / "2-0:1.0" / "usb2-port1"
    twin.mkdir(parents=True)
    (usb1 / "1-0:1.0" / "usb1-port1" / "peer").symlink_to(twin)
    found = reader.read_usb({"sdb": {"device_path": str(hub / "1-1:1.0" / "host9")}})
    assert found[str(hub)]["peer_hub"] == str(usb2)
    assert str(usb2) in found  # the twin's hub is captured: its speed is the socket's capability
    assert found[str(usb2)]["speed"] == "5000"


@pytest.mark.os_posix
def test_a_disk_not_on_usb_records_nothing(tmp_path: Path) -> None:
    assert reader.read_usb({"sda": {"device_path": "/sys/devices/pci0000:00/0000:00:17.0/ata1/host0"}}) == {}
    assert reader.read_usb({"zram0": {}}) == {}


@pytest.mark.os_agnostic
def test_the_usbfs_control_structure_matches_the_kernel_layout() -> None:
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert usbfs.UsbCtrlTransfer.data.offset == (16 if pointer == 8 else 12)
    assert ctypes.sizeof(usbfs.UsbCtrlTransfer) == (24 if pointer == 8 else 16)
    # _IOWR('U', 0, struct usbdevfs_ctrltransfer)
    expected = (3 << 30) | (ctypes.sizeof(usbfs.UsbCtrlTransfer) << 16) | (ord("U") << 8)
    assert expected == usbfs.USBDEVFS_CONTROL


@pytest.mark.os_posix
def test_a_stalled_bos_request_means_the_device_has_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    node = tmp_path / "003"
    node.write_bytes(b"")

    def stall(_fd: int, _request: int, _arg: object) -> int:
        raise OSError(errno.EPIPE, "Broken pipe")

    monkeypatch.setattr(fcntl, "ioctl", stall)  # the kernel is the true external edge here
    with pytest.raises(usbfs.NoBos):
        usbfs.read_bos(node)


@pytest.mark.os_posix
def test_a_refused_bos_request_is_an_error_not_an_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    node = tmp_path / "003"
    node.write_bytes(b"")

    def refuse(_fd: int, _request: int, _arg: object) -> int:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(fcntl, "ioctl", refuse)
    with pytest.raises(PermissionError):
        usbfs.read_bos(node)


@pytest.mark.os_posix
def test_a_bos_is_read_header_first_then_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    node = tmp_path / "003"
    node.write_bytes(b"")
    asked: list[int] = []

    def answer(_fd: int, _request: int, transfer: usbfs.UsbCtrlTransfer) -> int:
        asked.append(transfer.wLength)
        ctypes.memmove(transfer.data, BOS, transfer.wLength)
        return transfer.wLength

    monkeypatch.setattr(fcntl, "ioctl", answer)
    assert usbfs.read_bos(node) == BOS
    assert asked == [5, len(BOS)]


@pytest.mark.os_posix
def test_a_header_that_is_not_a_bos_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    node = tmp_path / "003"
    node.write_bytes(b"")

    def device_descriptor(_fd: int, _request: int, transfer: usbfs.UsbCtrlTransfer) -> int:
        ctypes.memmove(transfer.data, bytes.fromhex("1201000200"), transfer.wLength)
        return transfer.wLength

    monkeypatch.setattr(fcntl, "ioctl", device_descriptor)
    with pytest.raises(ValueError, match="1201000200"):
        usbfs.read_bos(node)


@pytest.mark.os_agnostic
def test_a_usb_speed_longer_than_any_device_publishes_is_refused(tmp_path: Path) -> None:
    crafted = {
        "schema": 2,
        "platform": "linux",
        "hostname": "box",
        "kernel": "x",
        "pci": {},
        "usb": {"/sys/devices/usb1": {"speed": "A" * (MAX_DEVICE_TEXT + 1)}},
    }
    path = tmp_path / "huge-usb.json"
    path.write_text(json.dumps(crafted), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="at most 4096"):
        load(path)
