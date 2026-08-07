"""Decoder tests against blobs captured from real hardware.

The fixtures in ``tests/fixtures/hw`` are read-only captures from two Proxmox
machines carrying 20 drives between them: Samsung SATA SSDs and Hitachi and WDC
spinning disks behind two LSI SAS host bus adapters, plus NVMe. Synthetic bytes
would not have caught the byte-swapped ATA strings or the old drive that reports
no negotiated speed at all, so the tests run on what the hardware actually said.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.decode.ata_identify import decode_identify, decode_vpd_ata_information
from lsdsk.adapters.hw.decode.ata_smart import decode_attributes, decode_health
from lsdsk.adapters.hw.decode.nvme import (
    decode_identify_controller,
    decode_smart_log,
)
from lsdsk.adapters.hw.decode.pciids import Database, describe, parse_pci_ids
from lsdsk.domain.enums import DiskKind

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"
FIXTURE_HOSTS = ("linux-sas-hba", "linux-minimal")


def load_capture(host: str) -> dict[str, Any]:
    """Load one captured machine."""
    with (FIXTURE_DIR / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


def blob(record: dict[str, str], key: str) -> bytes:
    """Decode one base64 blob out of a capture record."""
    return base64.b64decode(record[key])


def ata_records(host: str) -> list[tuple[str, dict[str, str]]]:
    """Every captured ATA device on one host."""
    capture = load_capture(host)
    return [(node, record) for node, record in capture["ata"].items() if "identify" in record]


ALL_ATA = [(host, node) for host in FIXTURE_HOSTS for node, _ in ata_records(host)]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("host", "node"), ALL_ATA)
def test_when_identify_is_decoded_it_yields_a_plausible_drive(host: str, node: str) -> None:
    """Verify every captured drive decodes to sane identity and geometry."""
    record = dict(ata_records(host))[node]
    identity = decode_identify(blob(record, "identify"))

    assert identity.model, f"{host}:{node} decoded an empty model"
    assert identity.serial, f"{host}:{node} decoded an empty serial"
    assert identity.firmware, f"{host}:{node} decoded an empty firmware revision"
    assert identity.sectors is not None and identity.sectors > 0
    assert identity.kind in (DiskKind.SSD, DiskKind.HDD)
    assert identity.size_bytes is not None and identity.size_bytes > 10**9


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("host", "node"), ALL_ATA)
def test_when_a_drive_is_read_two_ways_both_paths_agree(host: str, node: str) -> None:
    """Verify the unprivileged sysfs path matches the privileged ioctl path.

    ``vpd_pg89`` is world readable and carries the same IDENTIFY response that
    an ATA passthrough returns.  The two arrive through completely separate
    kernel paths, so agreement is real evidence that both the transport and the
    decoder are right, not merely self-consistent.
    """
    capture = load_capture(host)
    vpd = capture["block"].get(node, {}).get("vpd", {}).get("vpd_pg89")
    if vpd is None:
        pytest.skip(f"{host}:{node} exposes no ATA Information VPD page")

    from_ioctl = decode_identify(blob(dict(ata_records(host))[node], "identify"))
    from_sysfs = decode_vpd_ata_information(base64.b64decode(vpd))

    assert from_sysfs.model == from_ioctl.model
    assert from_sysfs.serial == from_ioctl.serial
    assert from_sysfs.firmware == from_ioctl.firmware
    assert from_sysfs.max_gbps == from_ioctl.max_gbps
    assert from_sysfs.sectors == from_ioctl.sectors


@pytest.mark.os_agnostic
def test_when_a_known_ssd_is_decoded_its_fields_match_the_label() -> None:
    """Verify a specific captured drive against what the hardware really is."""
    identity = decode_identify(blob(dict(ata_records("linux-sas-hba"))["sda"], "identify"))

    assert identity.model == "Samsung SSD 870 EVO 4TB"
    assert identity.firmware == "SVT03B6Q"
    assert identity.kind is DiskKind.SSD
    assert identity.max_gbps == 6.0
    assert identity.negotiated_gbps == 6.0
    assert identity.smart_supported


@pytest.mark.os_agnostic
def test_when_an_old_drive_reports_no_negotiated_speed_capability_still_decodes() -> None:
    """Verify a drive that leaves word 77 empty still yields its capability.

    The Hitachi in this machine reports its supported rates but not the rate it
    negotiated.  Treating that as "0 Gb/s" would raise a false alarm on every
    such drive, so the two fields are read independently.
    """
    identity = decode_identify(blob(dict(ata_records("linux-sas-hba"))["sdr"], "identify"))

    assert identity.model == "Hitachi HDS722020ALA330"
    assert identity.kind is DiskKind.HDD
    assert identity.rotation_rpm == 7200
    assert identity.max_gbps == 3.0
    assert identity.negotiated_gbps is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("host", "node"), ALL_ATA)
def test_when_smart_data_is_decoded_it_yields_attributes(host: str, node: str) -> None:
    """Verify every captured drive returns a usable SMART attribute table."""
    record = dict(ata_records(host))[node]
    attributes = decode_attributes(blob(record, "smart_data"), blob(record, "smart_thresholds"))

    assert attributes, f"{host}:{node} decoded an empty attribute table"
    identifiers = {attribute.id for attribute in attributes}
    assert 9 in identifiers, "power-on hours is present on every drive in this fleet"
    assert all(attribute.threshold is not None for attribute in attributes)


@pytest.mark.os_agnostic
def test_when_health_is_built_wear_and_hours_are_populated() -> None:
    """Verify the ATA attribute table folds into the cross-platform health model."""
    record = dict(ata_records("linux-sas-hba"))["sda"]
    health = decode_health(blob(record, "smart_data"), blob(record, "smart_thresholds"))

    # Power-on hours and wear only ever climb, so these are bounded rather than
    # exact: an exact figure would fail the next time the fixture is recaptured,
    # which is how this test first broke.
    assert health.power_on_hours is not None and health.power_on_hours > 15_000
    assert health.percent_used is not None and 0 <= health.percent_used <= 10
    assert health.reallocated_sectors == 0
    assert health.bytes_written is not None and health.bytes_written > 10**13


@pytest.mark.os_agnostic
def test_when_a_spinning_drive_is_decoded_its_temperature_is_plausible() -> None:
    """Verify the temperature attribute decodes to a real-world reading."""
    record = dict(ata_records("linux-sas-hba"))["sdr"]
    health = decode_health(blob(record, "smart_data"), blob(record, "smart_thresholds"))

    assert health.temperature_c is not None
    assert 15 <= health.temperature_c <= 70


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", FIXTURE_HOSTS)
def test_when_nvme_identify_is_decoded_it_names_the_drive(host: str) -> None:
    """Verify NVMe Identify Controller decodes on both captured machines."""
    capture = load_capture(host)
    node, record = next(iter(capture["nvme"].items()))
    identity = decode_identify_controller(blob(record, "identify_controller"))

    assert identity.model, f"{host}:{node} decoded an empty model"
    assert identity.serial
    assert identity.firmware


@pytest.mark.os_agnostic
def test_when_a_modern_nvme_reports_thresholds_they_are_ordered() -> None:
    """Verify vendor thermal thresholds decode from Kelvin into Celsius."""
    capture = load_capture("linux-minimal")
    identity = decode_identify_controller(blob(capture["nvme"]["nvme0n1"], "identify_controller"))

    assert identity.model == "Samsung SSD 990 PRO 4TB"
    assert identity.warning_temperature_c == 82
    assert identity.critical_temperature_c == 85


@pytest.mark.os_agnostic
def test_when_an_older_nvme_omits_thresholds_they_stay_unknown() -> None:
    """Verify an unreported threshold decodes to None rather than to 0 C.

    Pre-1.2 NVMe drives leave the composite temperature thresholds at zero.
    Reading that literally would put every such drive at "-273 C critical" and
    make the temperature rule fire on all of them, so zero means "not reported".
    """
    capture = load_capture("linux-sas-hba")
    identity = decode_identify_controller(blob(capture["nvme"]["nvme0n1"], "identify_controller"))

    assert identity.model == "SAMSUNG MZVPV512HDGL-00000"
    assert identity.warning_temperature_c is None
    assert identity.critical_temperature_c is None


@pytest.mark.os_agnostic
def test_when_a_worn_nvme_is_decoded_its_wear_is_reported() -> None:
    """Verify the NVMe health log on a drive that is genuinely well used."""
    capture = load_capture("linux-sas-hba")
    record = capture["nvme"]["nvme0n1"]
    identity = decode_identify_controller(blob(record, "identify_controller"))
    health = decode_smart_log(blob(record, "smart_log"), identity)

    assert identity.model == "SAMSUNG MZVPV512HDGL-00000"
    assert health.percent_used is not None and 50 <= health.percent_used <= 100
    assert health.power_on_hours is not None and health.power_on_hours > 47_000
    assert health.media_errors == 2
    assert health.temperature_c is not None and 10 <= health.temperature_c <= 90
    assert health.ok is True
    assert health.bytes_written is not None and health.bytes_written > 4 * 10**14


@pytest.mark.os_agnostic
def test_when_critical_warning_bits_are_set_they_are_named() -> None:
    """Verify the critical warning byte survives decoding and names its reasons.

    The byte used to be collapsed to a boolean at decode time, so the reasons
    could never reach a rule or a reader no matter how the drive was failing.
    """
    blob = bytearray(512)
    blob[0] = 0b1001
    health = decode_smart_log(bytes(blob))
    assert health.ok is False
    assert health.critical_warning == 0b1001
    assert health.critical_warning_reasons == (
        "spare capacity below threshold",
        "media placed in read-only mode",
    )
    assert decode_smart_log(bytes(512)).critical_warning_reasons == ()


@pytest.mark.os_agnostic
def test_the_nvme_counters_worth_trending_survive_decoding() -> None:
    """Verify power cycles, unsafe shutdowns and error log entries are kept.

    All three sit in the SMART log the decoder already reads and were being
    discarded. Offsets checked against `nvme smart-log` on live hardware.
    """
    blob = bytearray(512)
    blob[112:128] = (149).to_bytes(16, "little")
    blob[144:160] = (47).to_bytes(16, "little")
    blob[176:192] = (608).to_bytes(16, "little")
    blob[3], blob[4] = 100, 5
    health = decode_smart_log(bytes(blob))
    assert health.power_cycles == 149
    assert health.unsafe_shutdowns == 47
    assert health.error_log_entries == 608
    assert health.available_spare == 100
    assert health.available_spare_threshold == 5
    assert health.spare_below_threshold is False


@pytest.mark.os_agnostic
def test_spare_at_or_under_the_drives_own_threshold_is_flagged() -> None:
    blob = bytearray(512)
    blob[3], blob[4] = 4, 5
    assert decode_smart_log(bytes(blob)).spare_below_threshold is True


@pytest.mark.os_agnostic
def test_when_pci_ids_are_parsed_vendors_and_devices_resolve() -> None:
    """Verify the pci.ids parser handles nesting and ignores subsystem lines."""
    database = parse_pci_ids(
        "# a comment\n1000  Broadcom / LSI\n\t0097  SAS3008 PCI-Express Fusion-MPT SAS-3\n"
        "\t\t1000 3090  Subsystem name that must be ignored\n144d  Samsung Electronics\n"
    )
    vendors, devices = database

    assert vendors[0x1000] == "Broadcom / LSI"
    assert vendors[0x144D] == "Samsung Electronics"
    assert devices[(0x1000, 0x0097)] == "SAS3008 PCI-Express Fusion-MPT SAS-3"
    assert describe(0x1000, 0x0097, database) == "Broadcom / LSI SAS3008 PCI-Express Fusion-MPT SAS-3"


@pytest.mark.os_agnostic
def test_when_a_device_is_unknown_describe_falls_back_to_hex() -> None:
    """Verify an unknown identifier still renders as something printable."""
    assert describe(0xDEAD, 0xBEEF, Database({}, {})) == "Device dead:beef"


@pytest.mark.os_agnostic
def test_a_machine_with_no_system_pci_ids_still_names_its_controllers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundled database is what makes a Windows machine name a controller.

    None of the search paths exists on Windows, and before the database shipped
    with the package that left the operating system's own device description as
    the only source - which Windows localises. Every path is made unreachable
    here, which is that machine's state.
    """
    from lsdsk.adapters.hw.decode import pciids

    monkeypatch.setattr(pciids, "PCI_IDS_SEARCH_PATHS", ("/definitely/not/here",))
    pciids.reset_database_cache()
    try:
        assert pciids.find_pci_ids(pciids.PCI_IDS_SEARCH_PATHS) is None, "the probe found a file it must not"
        # A device, not just a vendor: the built-in fallback table holds vendors
        # only, so a vendor-only assertion passes with no database at all.
        assert "NVMe" in pciids.describe(0x144D, 0xA80A)
        assert "AHCI" in pciids.describe(0x8086, 0x7AE2)
    finally:
        pciids.reset_database_cache()


@pytest.mark.os_agnostic
def test_the_bundled_database_is_where_the_package_says_it_is() -> None:
    """A data file that resolves from the checkout can still be missing from the wheel.

    Nothing else in the suite would notice: the tests import from ``src``, where
    the file always exists. This asserts the path the package declares, so a
    dropped entry in the wheel's include list fails here rather than on a user's
    machine.
    """
    from lsdsk.adapters.hw.decode import pciids

    assert pciids.BUNDLED_PCI_IDS.name == "pci.ids.gz"
    assert pciids.read_bundled_pci_ids() is not None, "the bundled database did not decompress"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "IDENTIFY response is 0 bytes"),
        (bytes(511), "IDENTIFY response is 511 bytes"),
    ],
)
def test_when_identify_is_truncated_it_is_rejected(payload: bytes, message: str) -> None:
    """Verify a short buffer raises rather than decoding rubbish."""
    with pytest.raises(ValueError, match=message):
        decode_identify(payload)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("host", "node"), ALL_ATA)
def test_the_overall_verdict_matches_what_the_drive_would_say(host: str, node: str) -> None:
    """Verify the headline health verdict is computed for every captured drive.

    This is the line every SMART tool prints first, and it is not a separate
    reading: a drive is failing exactly when a graded attribute has fallen to or
    below its maker's threshold. `smartctl -H` reports PASSED for every drive in
    these captures, so every one must come out True rather than unknown.
    """
    record = dict(ata_records(host))[node]
    health = decode_health(blob(record, "smart_data"), blob(record, "smart_thresholds"))

    assert health.ok is True, f"{host}:{node} should agree with smartctl -H, which reports PASSED"


@pytest.mark.os_agnostic
def test_a_drive_below_its_threshold_reads_as_failing() -> None:
    """Verify the verdict flips when an attribute reaches its threshold."""
    from lsdsk.adapters.hw.decode.ata_smart import overall_health
    from lsdsk.domain.models import SmartAttribute

    healthy = (SmartAttribute(5, "Reallocated_Sector_Ct", 100, 100, 10, 0),)
    failing = (SmartAttribute(5, "Reallocated_Sector_Ct", 10, 10, 10, 4096),)
    # A threshold of zero means the attribute is advisory and never fails, no
    # matter how large its raw value grows.
    advisory = (SmartAttribute(199, "CRC_Error_Count", 1, 1, 0, 2_174_213),)

    assert overall_health(healthy) is True
    assert overall_health(failing) is False
    assert overall_health(advisory) is True
    assert overall_health(()) is None


@pytest.mark.os_agnostic
def test_values_match_the_reference_tools_for_a_known_drive() -> None:
    """Verify every field against what smartctl reports for the same drive.

    The figures on the right come from `smartctl -i` and `smartctl -A` run on
    linux-minimal. Agreeing with the tool everyone already trusts is the point:
    lsdsk reads the same structures by a different route, so a disagreement
    means one of the two decoders is wrong.
    """
    record = dict(ata_records("linux-minimal"))["sda"]
    identity = decode_identify(blob(record, "identify"))
    health = decode_health(blob(record, "smart_data"), blob(record, "smart_thresholds"))

    assert identity.model == "Samsung SSD 870 EVO 500GB"
    assert identity.firmware == "SVT02B6Q"
    assert identity.size_bytes == 500_107_862_016  # smartctl: 500,107,862,016 bytes
    assert identity.kind is DiskKind.SSD  # smartctl: Solid State Device
    assert identity.max_gbps == 6.0  # smartctl: SATA 3.3, 6.0 Gb/s
    assert identity.negotiated_gbps == 6.0  # smartctl: (current: 6.0 Gb/s)

    assert health.reallocated_sectors == 0  # attribute 5 raw
    assert health.crc_errors == 0  # attribute 199 raw
    assert health.ok is True  # smartctl -H: PASSED

    # Counters that only ever climb are bounded rather than pinned. The drive
    # wrote another 7640 sectors between the smartctl run quoted here and the
    # capture, which is exactly how an exact assertion on a live counter fails.
    assert health.percent_used is not None and 0 <= health.percent_used <= 10  # attribute 177: 96 normalised
    assert health.bytes_written is not None
    assert health.bytes_written >= 21_986_625_196 * 512 * 0.99  # attribute 241 raw, counted in sectors
