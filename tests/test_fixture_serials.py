"""A committed capture must carry one serial per drive, in every copy of it.

A capture records the same drive serial in up to eight places, because the
reader stores the raw structures it read rather than a decoded summary: the ATA
IDENTIFY serial field, the copy of IDENTIFY embedded in VPD page 0x89, VPD page
0x80, two designators of VPD page 0x83, the vendor-specific tail of the standard
INQUIRY response, the NVMe Identify Controller SN field, the subsystem NQN that
embeds it in a name string, and the sysfs ``wwid`` that embeds it as hex.

Fixtures are captures from real machines with the serials replaced, so a scrub
that reaches some of those and not others leaves the real identifier published
in the ones it missed AND makes the fixture disagree with itself. Both halves
were true of this repository: the scrub covered the ATA and NVMe fields and
missed the four SCSI/VPD copies, the NQN and the hex ``wwid``, which no text
search for the serial can find.

Enumerating the locations in prose is what failed, so this asserts the property
instead: every copy of a drive's serial in a fixture must agree with the one
the tool reports for that drive. A recapture that misses a location fails here
and is told which one, and a location added to the reader is covered the moment
a fixture carries it.

A disk behind USB has a SECOND serial, the USB device's own. Windows reports the
bridge's VPD 0x80 serial as the disk's serial and keeps the device's raw iSerial
in its instance ID,
behind ``MSFT30`` for a SuperSpeed device; on Linux every SCSI-layer copy (VPD
0x80, the INQUIRY vendor bytes, the VPD 0x83 T10 designator) is the bridge's.
It is not the drive's, so it is held to its own copies rather than to IDENTIFY,
and only IDENTIFY, which reaches the drive through the bridge, is the drive's. An NVMe disk on Windows
reports its namespace EUI-64 in that same field, which is no copy of the SN at
all and has no second copy to agree with.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from lsdsk.adapters.hw.decode.ata_identify import decode_identify, decode_vpd_ata_information
from lsdsk.adapters.hw.decode.nvme import decode_identify_controller
from lsdsk.adapters.hw.decode.usb import decode_bos

if TYPE_CHECKING:
    from collections.abc import Mapping

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# The fixtures carry this many serial copies between them. A check that decodes
# none of them passes on anything, so the floor is asserted rather than assumed.
_LOCATIONS_THE_FIXTURES_CARRY = 100

# A serial no drive here carries, for the control to plant.
_ANOTHER_SERIAL = b"S0METHINGELSE00     "

_INQUIRY_SERIAL_START = 36
_INQUIRY_SERIAL_END = 56
_NQN_START = 768
_NQN_END = 1024
# SAT-4: a T10 vendor-id designator for an ATA device is "ATA     " (8 bytes),
# the 40-byte IDENTIFY model, then the 20-byte IDENTIFY serial. Any other shape
# has no fixed serial offset, so it is left alone rather than guessed at.
_SAT_VENDOR = b"ATA     "
_SAT_DESIGNATOR_LENGTH = 68
_SAT_SERIAL_START = 48
# A bridge's own T10 vendor-id designator is vendor (8), product (16), serial.
_T10_VENDOR_PRODUCT_LENGTH = 24

# A USB device instance ID is USB\VID_xxxx&PID_xxxx\<iSerial>. Windows prefixes a
# SuperSpeed device's iSerial with MSFT30, and a device with no serial gets a
# generated segment holding '&', which names no serial and is skipped.
_USB_INSTANCE = re.compile(r"^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\(?P<segment>[^\\&]+)$", re.IGNORECASE)
_SUPERSPEED_PREFIX = "MSFT30"
# A Linux disk behind USB resolves through a usbN directory in sysfs.
_LINUX_USB_PATH = re.compile(r"/usb\d+/")

# The Container ID every committed capture carries in place of the real one.
FAKE_CONTAINER_ID = bytes(range(16))


def _text(raw: bytes) -> str:
    """The printable reading of a raw field, as a SCSI ASCII field is space padded."""
    return "".join(chr(byte) if 32 <= byte < 127 else " " for byte in raw).strip()


def _is_sat_designator(data: bytes) -> bool:
    """Whether this T10 designator has the SAT layout whose serial offset is fixed."""
    return len(data) == _SAT_DESIGNATOR_LENGTH and data.startswith(_SAT_VENDOR)


def _page_83_bridge_serials(blob: bytes) -> dict[str, str]:
    """Every T10 vendor-id designator in VPD 0x83 that is not the SAT layout, by offset.

    Only read for a disk behind USB, where the bridge answers the page and
    writes vendor, product, then its own serial.
    """
    found: dict[str, str] = {}
    offset = 4
    while offset + 4 <= len(blob):
        code_set, designator_type, length = blob[offset] & 0xF, blob[offset + 1] & 0xF, blob[offset + 3]
        data = blob[offset + 4 : offset + 4 + length]
        if code_set == 2 and designator_type == 1 and not _is_sat_designator(data):
            found[f"usb/vpd_pg83[{offset}] t10-vendor-id"] = _text(data[_T10_VENDOR_PRODUCT_LENGTH:])
        offset += 4 + length
    return found


def _page_83_serials(blob: bytes) -> dict[str, str]:
    """Every ASCII designator in VPD page 0x83, keyed by its offset."""
    found: dict[str, str] = {}
    offset = 4
    while offset + 4 <= len(blob):
        code_set, designator_type, length = blob[offset] & 0xF, blob[offset + 1] & 0xF, blob[offset + 3]
        data = blob[offset + 4 : offset + 4 + length]
        if code_set == 2 and designator_type == 0:
            found[f"vpd_pg83[{offset}] vendor-specific"] = _text(data)
        elif code_set == 2 and designator_type == 1 and _is_sat_designator(data):
            found[f"vpd_pg83[{offset}] t10-vendor-id"] = _text(data[_SAT_SERIAL_START:])
        offset += 4 + length
    return found


def _mapping(value: object) -> dict[str, object]:
    """The mapping a JSON value holds, or an empty one, so every lookup stays typed."""
    if not isinstance(value, dict):
        return {}
    items = cast("dict[object, object]", value).items()
    return {str(key): item for key, item in items}


def _device(capture: Mapping[str, object], section: str, device: str) -> dict[str, object]:
    """One device's record from a top-level section of a capture."""
    return _mapping(_mapping(capture.get(section)).get(device))


def _records(
    capture: Mapping[str, object], device: str
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """The ata, nvme and block records for one drive, on either platform's layout.

    A Linux capture keys top-level ``ata``, ``nvme`` and ``block`` sections by
    device name. A Windows capture has none of those: every blob is nested
    inside its own entry of ``disks``. Reading only the Linux shape is what made
    a Windows capture yield nothing at all.
    """
    disks = _mapping(capture.get("disks"))
    if device in disks:
        disk = _mapping(disks.get(device))
        return _mapping(disk.get("ata")), _mapping(disk.get("nvme")), disk
    return (
        _device(capture, "ata", device),
        _device(capture, "nvme", device),
        _device(capture, "block", device),
    )


def _blob(source: Mapping[str, object], key: str) -> bytes | None:
    value = source.get(key)
    return base64.b64decode(value) if isinstance(value, str) else None


def _usb_segment_serial(instance_id: str) -> str | None:
    """The serial a USB device instance ID carries, or None when it carries none.

    The segment is the device's raw iSerial. The bridge measured here sends that
    as the hex text of the serial it reports in VPD 0x80 (Linux sysfs shows the
    same 24 digits), so hex text is decoded; that is this bridge's firmware, not
    a Windows rule, and a bridge that sends anything else is taken verbatim -
    where it disagrees, the check names it rather than passing.
    """
    match = _USB_INSTANCE.match(instance_id)
    if match is None:
        return None
    segment = match["segment"]
    if not segment.upper().startswith(_SUPERSPEED_PREFIX):
        return segment
    body = segment[len(_SUPERSPEED_PREFIX) :]
    try:
        return bytes.fromhex(body).decode("ascii")
    except ValueError:
        return body


def _usb_instance_ids(capture: Mapping[str, object], device: str) -> dict[str, str]:
    """Every copy of a disk's USB device instance ID, keyed by where it lives."""
    record = _mapping(_mapping(capture.get("disks")).get(device))
    found: dict[str, str] = {}
    parent = record.get("parent")
    if isinstance(parent, str):
        found["parent"] = parent
    ancestors = record.get("ancestors")
    if isinstance(ancestors, list):
        for index, ancestor in enumerate(cast("list[object]", ancestors)):
            if isinstance(ancestor, str):
                found[f"ancestors[{index}]"] = ancestor
    own = {instance for instance in found.values() if _USB_INSTANCE.match(instance)}
    for key in _mapping(capture.get("usb_ports")):
        if key in own:
            found[f"usb_ports[{key}]"] = key
    return {where: instance for where, instance in found.items() if _USB_INSTANCE.match(instance)}


def _behind_usb_on_linux(capture: Mapping[str, object], device: str) -> bool:
    """Whether a Linux disk's sysfs path runs through a USB device."""
    path = _device(capture, "block", device).get("device_path")
    return isinstance(path, str) and _LINUX_USB_PATH.search(path) is not None


def _scsi_locations(record: Mapping[str, object]) -> dict[str, str]:
    """The serial copies the SCSI layer answers with: VPD 0x80, VPD 0x83 and INQUIRY."""
    vpd = _mapping(record.get("vpd"))
    found: dict[str, str] = {}
    if (page_80 := _blob(vpd, "vpd_pg80")) is not None:
        found["vpd_pg80"] = _text(page_80[4 : 4 + page_80[3]])
    if (page_83 := _blob(vpd, "vpd_pg83")) is not None:
        found.update(_page_83_serials(page_83))
    if (inquiry := _blob(vpd, "inquiry")) is not None and len(inquiry) >= _INQUIRY_SERIAL_END:
        found["inquiry"] = _text(inquiry[_INQUIRY_SERIAL_START:_INQUIRY_SERIAL_END])
    return found


def _usb_serial_locations(capture: Mapping[str, object], device: str) -> dict[str, str]:
    """Every copy of the serial of the USB device a disk sits behind.

    Empty for a disk with no USB device above it. Windows reports this serial as
    the disk's own, so ``device/serial`` belongs here rather than to the drive;
    on Linux the bridge answers every SCSI page, so those copies do.
    """
    if _behind_usb_on_linux(capture, device):
        _, _, record = _records(capture, device)
        found = {f"usb/{where}": serial for where, serial in _scsi_locations(record).items()}
        if (page_83 := _blob(_mapping(record.get("vpd")), "vpd_pg83")) is not None:
            found.update(_page_83_bridge_serials(page_83))
        return {where: serial for where, serial in found.items() if serial}
    instances = _usb_instance_ids(capture, device)
    if not instances:
        return {}
    found = {
        f"usb/{where}": serial for where, instance in instances.items() if (serial := _usb_segment_serial(instance))
    }
    reported = _reported_serial(capture, device)
    if reported:
        found["device/serial"] = reported
    return found


def _usb_published(locations: Mapping[str, str]) -> str | None:
    """The USB device's serial as the platform published it: Windows' field, Linux's VPD 0x80."""
    return locations.get("device/serial") or locations.get("usb/vpd_pg80")


def _reported_serial(capture: Mapping[str, object], device: str) -> str | None:
    """The serial the platform published for a disk, beside the decoded structures."""
    _, _, record = _records(capture, device)
    reported = _mapping(record.get("device")).get("serial")
    return reported.strip() if isinstance(reported, str) and reported.strip() else None


def _reported_belongs_to_the_drive(capture: Mapping[str, object], device: str) -> bool:
    """Whether the platform's own serial field is a copy of the drive's serial.

    On Windows it is not, twice: behind USB it is the USB device's serial, and
    for NVMe it is the namespace EUI-64 the storage driver publishes there.
    """
    if _usb_instance_ids(capture, device):
        return False
    _, nvme, _ = _records(capture, device)
    is_windows = device in _mapping(capture.get("disks"))
    return not (is_windows and _blob(nvme, "identify_controller") is not None)


def _serial_locations(capture: Mapping[str, object], device: str) -> dict[str, str]:
    """Every copy of a drive's own serial this capture holds, keyed by where it lives."""
    ata, nvme, record = _records(capture, device)
    vpd = _mapping(record.get("vpd"))
    found: dict[str, str] = {}

    reported = _reported_serial(capture, device)
    if reported and _reported_belongs_to_the_drive(capture, device):
        found["device/serial"] = reported

    if (identify := _blob(ata, "identify")) is not None:
        found["ata/identify"] = decode_identify(identify).serial
    if (page_89 := _blob(vpd, "vpd_pg89")) is not None:
        found["vpd_pg89"] = decode_vpd_ata_information(page_89).serial
    if not _behind_usb_on_linux(capture, device):
        found.update(_scsi_locations(record))
    if (controller := _blob(nvme, "identify_controller")) is not None:
        found["nvme/sn"] = decode_identify_controller(controller).serial
        nqn = _text(controller[_NQN_START:_NQN_END])
        if ":" in nqn:
            found["nvme/nqn"] = nqn.rsplit(":", 1)[-1].strip()
    wwid = record.get("wwid")
    if isinstance(wwid, str) and wwid.startswith("nvme."):
        parts = wwid.split("-")
        if len(parts) >= 2:
            found["wwid"] = _text(bytes.fromhex(parts[1]))
    return {where: serial for where, serial in found.items() if serial}


def _devices(capture: Mapping[str, object]) -> list[str]:
    """Every drive a capture records, whichever platform wrote it.

    One enumeration, because two copies of it is how a whole platform came to be
    skipped: the Linux sections were named here and the Windows one was not, so
    every check below scored a Windows capture zero and passed.
    """
    return sorted(
        set(_mapping(capture.get("block"))) | set(_mapping(capture.get("nvme"))) | set(_mapping(capture.get("disks")))
    )


def _disagreements(capture: Mapping[str, object]) -> list[str]:
    """Locations whose serial differs from the one the tool reports for that drive.

    A USB device's serial copies are held to the one the platform published, as
    that serial has no decoded structure behind it to be held to. A copy agrees
    when it CONTAINS that serial, since a bridge wraps it in vendor bytes
    (measured: the USB product ID before it in INQUIRY, vendor and product before
    it in VPD 0x83). A scrub that missed a copy still leaves the real serial
    there, which does not contain the fake.
    """
    problems: list[str] = []
    for device in _devices(capture):
        locations = _serial_locations(capture, device)
        reported = locations.get("ata/identify") or locations.get("nvme/sn")
        if reported:
            problems += [
                f"{device} {where}={serial!r} but the drive reports {reported!r}"
                for where, serial in sorted(locations.items())
                if serial != reported
            ]
        usb = _usb_serial_locations(capture, device)
        published = _usb_published(usb)
        problems += [
            f"{device} {where}={serial!r} but the USB device reports {published!r}"
            for where, serial in sorted(usb.items())
            if published and published not in serial
        ]
    return problems


def _all_locations(capture: Mapping[str, object], device: str) -> dict[str, str]:
    """Every serial copy for one disk, the drive's own and its USB device's."""
    return {**_serial_locations(capture, device), **_usb_serial_locations(capture, device)}


def _bos_blobs(capture: Mapping[str, object]) -> dict[str, bytes]:
    """Every BOS descriptor a capture holds, on either platform, keyed by where it lives."""
    found: dict[str, bytes] = {}
    for section in ("usb_ports", "usb"):
        for key, entry in _mapping(capture.get(section)).items():
            if (bos := _blob(_mapping(entry), "bos")) is not None:
                found[f"{section}[{key}]"] = bos
    return found


def _real_container_ids(capture: Mapping[str, object]) -> list[str]:
    """BOS descriptors whose Container ID is not the placeholder a scrub writes."""
    return [
        where
        for where, bos in _bos_blobs(capture).items()
        if (container := decode_bos(bos).container_id) is not None and container != FAKE_CONTAINER_ID
    ]


def _fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _load(path: Path) -> dict[str, object]:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    return _mapping(parsed)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda path: path.stem)
def test_every_copy_of_a_serial_in_a_fixture_agrees_with_the_drive(fixture: Path) -> None:
    """No committed capture may hold two different serials for one drive."""
    problems = _disagreements(_load(fixture))
    assert not problems, f"{fixture.name} disagrees with itself:\n" + "\n".join(problems)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda path: path.stem)
def test_every_fixture_contributes_at_least_one_serial_location(fixture: Path) -> None:
    """A capture the extractor cannot read scores zero and passes, so require one each.

    The floor below is a SUM across every fixture, which the Linux captures meet
    on their own - so a capture whose shape this file cannot read at all was
    invisible to it, and the agreement check above silently inspected nothing.
    That is an ANY test guarding an EVERY invariant. This asserts it per capture,
    so a new platform or a changed section name fails here and names the file.
    """
    capture = _load(fixture)
    counts = {device: len(_all_locations(capture, device)) for device in _devices(capture)}
    assert sum(counts.values()) >= 1, (
        f"{fixture.name} yielded no serial locations, so every check over it passes vacuously; "
        f"devices found: {sorted(counts)}"
    )


@pytest.mark.os_agnostic
def test_a_fixture_carries_enough_locations_for_the_check_to_mean_something() -> None:
    """A check that finds no locations passes on anything, so require the copies."""
    counts: dict[str, int] = {}
    for path in _fixtures():
        capture = _load(path)
        counts[path.stem] = sum(len(_all_locations(capture, device)) for device in _devices(capture))
    assert sum(counts.values()) >= _LOCATIONS_THE_FIXTURES_CARRY, counts


@pytest.mark.os_agnostic
def test_the_check_names_a_location_planted_with_another_serial() -> None:
    """The control: a passing run above must mean the check could have failed."""
    capture = _load(FIXTURES / "linux-sas-hba.json")
    block = _mapping(capture.get("block"))
    record = _mapping(block.get("sda"))
    vpd = _mapping(record.get("vpd"))
    page_80 = _blob(vpd, "vpd_pg80")
    assert page_80 is not None, "the control needs a fixture that carries page 0x80"

    planted = page_80[:4] + _ANOTHER_SERIAL[: len(page_80) - 4]
    vpd["vpd_pg80"] = base64.b64encode(planted).decode("ascii")
    record["vpd"] = vpd
    block["sda"] = record
    capture["block"] = block

    problems = _disagreements(capture)

    assert any("sda vpd_pg80=" in problem for problem in problems), problems


@pytest.mark.os_agnostic
def test_the_check_names_a_planted_serial_on_a_windows_capture_too() -> None:
    """The Windows branch needs its own control, or only the Linux one is proven.

    A capture whose layout the extractor cannot read scores zero and passes, so
    the Linux control above says nothing about the Windows path. This plants a
    different serial in the field Windows reports and requires the check to name
    it, which is what makes a passing Windows fixture mean anything.
    """
    capture = _load(FIXTURES / "windows-ahci.json")
    disks = _mapping(capture.get("disks"))
    device = next(iter(sorted(disks)), None)
    assert device is not None, "the control needs a Windows capture that carries a disk"

    record = _mapping(disks.get(device))
    assert _blob(_mapping(record.get("ata")), "identify") is not None, (
        "the control needs a disk whose ATA IDENTIFY is present to disagree with"
    )
    record["device"] = {**_mapping(record.get("device")), "serial": _ANOTHER_SERIAL.decode("ascii").strip()}
    disks[device] = record
    capture["disks"] = disks

    problems = _disagreements(capture)

    assert any("device/serial=" in problem for problem in problems), problems


def _usb_disk(capture: dict[str, object]) -> tuple[dict[str, object], str]:
    """The disks section of a capture and the one disk in it that sits behind USB."""
    disks = _mapping(capture.get("disks"))
    device = next((name for name in sorted(disks) if _usb_instance_ids(capture, name)), None)
    assert device is not None, "the control needs a Windows capture with a disk behind USB"
    return disks, device


@pytest.mark.os_agnostic
def test_a_usb_device_serial_is_read_from_its_instance_id() -> None:
    """The instance ID copy must be decoded, or a scrub that misses it passes.

    The USB serial is held only to its own copies, so a decoder that reads
    nothing from the instance ID would leave ``device/serial`` alone and agree
    with itself on any capture.
    """
    capture = _load(FIXTURES / "windows-usb-uas.json")
    _, device = _usb_disk(capture)

    locations = _usb_serial_locations(capture, device)

    assert {"device/serial", "usb/parent", "usb/ancestors[0]"} <= set(locations), locations
    assert any(where.startswith("usb/usb_ports[") for where in locations), locations


@pytest.mark.os_agnostic
def test_the_check_names_a_usb_instance_id_planted_with_another_serial() -> None:
    """The control for the USB serial: a scrub that missed one copy must be named."""
    capture = _load(FIXTURES / "windows-usb-uas.json")
    disks, device = _usb_disk(capture)
    record = _mapping(disks.get(device))
    parent = record.get("parent")
    assert isinstance(parent, str)
    vendor_product = parent.rsplit("\\", 1)[0]
    record["parent"] = f"{vendor_product}\\{_SUPERSPEED_PREFIX}{_ANOTHER_SERIAL.strip().hex().upper()}"
    disks[device] = record
    capture["disks"] = disks

    problems = _disagreements(capture)

    assert any("usb/parent=" in problem for problem in problems), problems


@pytest.mark.os_agnostic
def test_a_usb_disk_s_reported_serial_is_not_held_to_the_drive_inside() -> None:
    """Behind USB, Windows reports the bridge's serial, so the drive's is not its copy.

    Held to the drive it would disagree on every real USB disk, since the two are
    different devices. Here the drive's own IDENTIFY is still read, and is then
    its only copy, so it has nothing to disagree with: the USB copies carry the
    check for this disk.
    """
    capture = _load(FIXTURES / "windows-usb-uas.json")
    _, device = _usb_disk(capture)

    drive = _serial_locations(capture, device)

    assert "device/serial" not in drive, drive
    assert set(drive) == {"ata/identify"}, drive
    assert "device/serial" in _usb_serial_locations(capture, device)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda path: path.stem)
def test_no_fixture_publishes_a_real_usb_container_id(fixture: Path) -> None:
    """A BOS Container ID is unique to one device, so a scrub replaces it with the placeholder."""
    assert not _real_container_ids(_load(fixture)), f"{fixture.name} carries a real Container ID"


@pytest.mark.os_agnostic
def test_the_container_id_check_names_a_planted_one() -> None:
    """The control: no committed disk's BOS carries a Container ID, so plant one.

    A Container ID capability is a 20-byte record of type 4 appended to the BOS,
    with the descriptor's total length raised to match.
    """
    capture = _load(FIXTURES / "windows-usb-uas.json")
    ports = _mapping(capture.get("usb_ports"))
    key = next(iter(sorted(ports)))
    port = _mapping(ports.get(key))
    bos = _blob(port, "bos")
    assert bos is not None and decode_bos(bos).container_id is None
    record = bytes([20, 0x10, 4, 0]) + bytes(range(100, 116))
    total = len(bos) + len(record)
    planted = bos[:2] + total.to_bytes(2, "little") + bytes([bos[4] + 1]) + bos[5:] + record
    assert decode_bos(planted).container_id == bytes(range(100, 116))

    port["bos"] = base64.b64encode(planted).decode("ascii")
    ports[key] = port
    capture["usb_ports"] = ports

    assert _real_container_ids(capture) == [f"usb_ports[{key}]"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("instance_id", "serial"),
    [
        ("USB\\VID_0781&PID_558C\\MSFT30" + b"FAKE00000002".hex().upper(), "FAKE00000002"),
        ("USB\\VID_0781&PID_5581\\4C530001230101100000", "4C530001230101100000"),
        ("USB\\VID_046D&PID_C52B\\5&1A2B3C4D&0&2", None),
        ("USB\\ROOT_HUB30\\4&2FD48294&0&0", None),
    ],
    ids=["superspeed-hex", "high-speed-verbatim", "generated-no-serial", "not-a-device"],
)
def test_a_usb_instance_id_is_read_the_way_windows_writes_it(instance_id: str, serial: str | None) -> None:
    """Each shape of the last segment, since a fixture holds only the SuperSpeed one."""
    assert _usb_segment_serial(instance_id) == serial


@pytest.mark.os_agnostic
def test_a_linux_usb_disk_holds_its_scsi_copies_to_the_bridge() -> None:
    """Behind USB on Linux the bridge answers every SCSI page; only IDENTIFY is the drive's."""
    capture = _load(FIXTURES / "linux-usb-ehci.json")

    assert set(_serial_locations(capture, "sdb")) == {"ata/identify"}
    assert set(_usb_serial_locations(capture, "sdb")) == {
        "usb/vpd_pg80",
        "usb/inquiry",
        "usb/vpd_pg83[40] t10-vendor-id",
    }
    # The control: the SATA disk on the same machine keeps its SCSI copies.
    assert {"vpd_pg80", "vpd_pg89"} <= set(_serial_locations(capture, "sda"))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("page", "start"), [("inquiry", _INQUIRY_SERIAL_START), ("vpd_pg83", 68)])
def test_the_check_names_a_bridge_copy_planted_with_another_serial(page: str, start: int) -> None:
    """A copy a scrub missed still holds the real serial, so it must not contain the fake.

    Each bridge copy wraps the serial in vendor bytes, so it is checked by
    containment; planting another serial over the bridge's own must be named.
    VPD 0x83's T10 designator starts at byte 44 here and the serial 24 bytes in.
    """
    capture = _load(FIXTURES / "linux-usb-ehci.json")
    block = _mapping(capture.get("block"))
    record = _mapping(block.get("sdb"))
    vpd = _mapping(record.get("vpd"))
    blob = _blob(vpd, page)
    assert blob is not None
    planted = blob[:start] + _ANOTHER_SERIAL[:12] + blob[start + 12 :]
    vpd[page] = base64.b64encode(planted).decode("ascii")
    record["vpd"] = vpd
    block["sdb"] = record
    capture["block"] = block

    problems = _disagreements(capture)

    assert any(f"sdb usb/{page}" in problem for problem in problems), problems
