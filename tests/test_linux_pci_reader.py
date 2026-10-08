"""The Linux PCI/class sysfs walk, driven against a synthetic sysfs tree.

`read_pci` and `read_classes` take a sysfs root "overridable for a test", but
nothing called them with one: the glue that resolves a driver symlink, filters
PCI-address-shaped children from the rest of a device directory, dispatches
the AHCI register read, and assembles a PCIe capability out of config space
ran only against whatever `/sys` the CI host happened to have. Everything
here builds that tree under `tmp_path`, the same pattern `test_hw_reader.py`
and `test_linux_mounts_reader.py` use for `read_block` and `mounts`, so these
tests run the same whatever machine they run on.

Driver symlinks and PCI-address directory names are POSIX-flavoured, so this
whole module is `os_posix`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lsdsk.adapters.hw.decode import ahci
from lsdsk.adapters.hw.linux.builder import build_controllers
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.reader import read_classes, read_pci
from lsdsk.adapters.hw.snapshot import parse_capture

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

pytestmark = pytest.mark.os_posix

# A PCI Express capability at offset 0x40 of an otherwise empty config space,
# built the same way `tests/test_slots.py` does for `parse_pcie_capability`
# directly - this module drives it from a sysfs `config` file instead.
_CAP_OFFSET = 0x40


def _pcie_config(*, slot_implemented: bool, slot_number: int) -> bytes:
    """A 256-byte PCI config space carrying one PCI Express capability."""
    config = bytearray(256)
    config[0x34] = _CAP_OFFSET
    config[_CAP_OFFSET] = 0x10  # PCI Express capability id
    capabilities = (1 << 8) if slot_implemented else 0
    config[_CAP_OFFSET + 2 : _CAP_OFFSET + 4] = capabilities.to_bytes(2, "little")
    slot_capabilities = slot_number << 19
    config[_CAP_OFFSET + 0x14 : _CAP_OFFSET + 0x18] = slot_capabilities.to_bytes(4, "little")
    return bytes(config)


def _ahci_registers(*, capability: int, ports_implemented: int) -> bytes:
    """A BAR5 region holding the two registers `read_ahci_capabilities` reads."""
    registers = bytearray(0x1000)
    registers[ahci.CAPABILITY_OFFSET : ahci.CAPABILITY_OFFSET + 4] = capability.to_bytes(4, "little")
    registers[ahci.PORTS_IMPLEMENTED_OFFSET : ahci.PORTS_IMPLEMENTED_OFFSET + 4] = ports_implemented.to_bytes(
        4, "little"
    )
    return bytes(registers)


def _device(root: Path, address: str, **attrs: str) -> Path:
    """One PCI device directory under `root`, with whichever plain-text attributes given."""
    path = root / address
    path.mkdir(parents=True, exist_ok=True)
    for name, value in attrs.items():
        (path / name).write_text(value + "\n", encoding="utf-8")
    return path


@pytest.fixture
def fixture_pci_keys() -> set[str]:
    """Every key a real captured `pci` entry has carried, across the committed fixtures.

    Proves the synthetic tree is not inventing a shape the real reader never
    produces: a key this module's entries carry must already be one the real
    reader writes somewhere.
    """
    keys: set[str] = set()
    for path in FIXTURES.glob("linux-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("pci", {}).values():
            keys |= set(entry.keys())
    assert keys, "no linux-*.json fixture carried any pci entry to compare against"
    return keys


@pytest.fixture
def fixture_class_keys() -> dict[str, set[str]]:
    """Every attribute key a real captured `classes` entry has carried, per class."""
    by_class: dict[str, set[str]] = {}
    for path in FIXTURES.glob("linux-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for class_name, members in data.get("classes", {}).items():
            bucket = by_class.setdefault(class_name, set())
            for entry in members.values():
                bucket |= set(entry.keys())
    assert by_class, "no linux-*.json fixture carried any classes entry to compare against"
    return by_class


class TestDriverResolution:
    """A `driver` symlink names the bound driver; its absence carries no key at all."""

    def test_a_driver_symlink_is_resolved_to_the_drivers_own_name(self, tmp_path: Path) -> None:
        device = _device(tmp_path, "0000:00:1f.2", **{"class": "0x010601"})
        driver_dir = tmp_path / "drivers" / "ahci"
        driver_dir.mkdir(parents=True)
        (device / "driver").symlink_to(driver_dir)

        entry = read_pci(tmp_path)["0000:00:1f.2"]

        assert entry["driver"] == "ahci"

    def test_a_device_with_no_driver_symlink_carries_no_driver_key(self, tmp_path: Path) -> None:
        _device(tmp_path, "0000:00:00.0", **{"class": "0x060000"})

        entry = read_pci(tmp_path)["0000:00:00.0"]

        assert "driver" not in entry

    def test_the_driver_key_is_one_the_real_reader_writes(self, fixture_pci_keys: set[str]) -> None:
        """RED control: `driver` is a key the real reader really writes somewhere."""
        assert "driver" in fixture_pci_keys


class TestChildEnumeration:
    """Only a PCI-address-shaped entry is a child; everything else in the directory is not."""

    def test_pci_address_shaped_entries_are_collected_as_children(self, tmp_path: Path) -> None:
        bridge = _device(tmp_path, "0000:00:01.0", **{"class": "0x060400"})
        (bridge / "0000:05:00.0").mkdir()
        (bridge / "0000:05:00.1").mkdir()

        entry = read_pci(tmp_path)["0000:00:01.0"]

        assert entry["children"] == ["0000:05:00.0", "0000:05:00.1"]

    def test_non_address_shaped_entries_are_never_collected_as_children(self, tmp_path: Path) -> None:
        bridge = _device(tmp_path, "0000:00:01.0", **{"class": "0x060400"})
        (bridge / "0000:05:00.0").mkdir()
        # Real sysfs directories that sit beside the children but are not one:
        # a single colon (msi_irqs-style name would have none, but a bus alias
        # can carry exactly one) and a plain attribute directory.
        (bridge / "msi_irqs").mkdir()
        (bridge / "power").mkdir()
        (bridge / "0000:05").mkdir()  # one colon only - not address-shaped

        entry = read_pci(tmp_path)["0000:00:01.0"]

        assert entry["children"] == ["0000:05:00.0"]

    def test_an_address_named_file_or_dangling_link_is_never_collected_as_a_child(self, tmp_path: Path) -> None:
        bridge = _device(tmp_path, "0000:00:01.0", **{"class": "0x060400"})
        (bridge / "0000:05:00.0").mkdir()
        (bridge / "0000:05:00.1").write_text("not a directory\n", encoding="utf-8")
        (bridge / "0000:05:00.2").symlink_to(tmp_path / "nowhere")

        entry = read_pci(tmp_path)["0000:00:01.0"]

        assert entry["children"] == ["0000:05:00.0"]

    def test_the_entry_path_is_the_resolved_device_directory(self, tmp_path: Path) -> None:
        real = _device(tmp_path / "devices" / "pci0000:00" / "0000:00:01.0", "0000:01:00.0", **{"class": "0x010601"})
        bus = tmp_path / "bus"
        bus.mkdir()
        (bus / "0000:01:00.0").symlink_to(real)

        entry = read_pci(bus)["0000:01:00.0"]

        assert entry["path"] == os.path.realpath(real)
        assert entry["path"] != str(bus / "0000:01:00.0")

    def test_the_resolved_path_is_what_names_a_controllers_upstream_bridge(self, tmp_path: Path) -> None:
        real = _device(tmp_path / "devices" / "pci0000:00" / "0000:00:01.0", "0000:01:00.0", **{"class": "0x010601"})
        bus = tmp_path / "bus"
        bus.mkdir()
        (bus / "0000:01:00.0").symlink_to(real)
        capture = parse_capture(
            {"schema": 2, "platform": "linux", "hostname": "example", "kernel": "6.1.0", "pci": read_pci(bus)}
        )
        assert isinstance(capture, LinuxCapture)

        (controller,) = build_controllers(capture)

        assert controller.upstream_address == "0000:00:01.0"

    def test_a_device_with_no_children_carries_no_children_key(self, tmp_path: Path) -> None:
        _device(tmp_path, "0000:00:1f.2", **{"class": "0x010601"})

        entry = read_pci(tmp_path)["0000:00:1f.2"]

        assert "children" not in entry


class TestAhciDispatch:
    """`read_pci` dispatches the AHCI register read only for an AHCI-class device."""

    def test_an_ahci_class_device_assembles_its_registers_into_the_entry(self, tmp_path: Path) -> None:
        device = _device(tmp_path, "0000:00:1f.2", **{"class": "0x010601"})
        (device / "resource5").write_bytes(_ahci_registers(capability=0xE7234F05, ports_implemented=0x3))

        entry = read_pci(tmp_path)["0000:00:1f.2"]

        assert entry["ahci"] == {"capability": 0xE7234F05, "ports_implemented": 0x3}
        assert "ahci_error" not in entry

    def test_an_ahci_class_device_whose_region_is_denied_records_the_refusal(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root may open any mode, so the refusal cannot be planted")
        device = _device(tmp_path, "0000:00:1f.2", **{"class": "0x010601"})
        (device / "resource5").write_bytes(bytes(0x1000))
        (device / "resource5").chmod(0o000)

        try:
            entry = read_pci(tmp_path)["0000:00:1f.2"]
        finally:
            (device / "resource5").chmod(0o600)

        assert "ahci" not in entry
        assert entry.get("ahci_error")

    def test_an_ahci_class_device_with_no_bar5_carries_neither_ahci_key(self, tmp_path: Path) -> None:
        """No region is a controller with nothing to give, not a refusal."""
        _device(tmp_path, "0000:00:1f.2", **{"class": "0x010601"})

        entry = read_pci(tmp_path)["0000:00:1f.2"]

        assert "ahci" not in entry
        assert "ahci_error" not in entry

    def test_a_non_ahci_class_device_is_never_sent_through_the_ahci_read(self, tmp_path: Path) -> None:
        device = _device(tmp_path, "0000:00:00.0", **{"class": "0x060000"})
        # If the class filter were missing, this would be read as registers.
        (device / "resource5").write_bytes(_ahci_registers(capability=1, ports_implemented=1))

        entry = read_pci(tmp_path)["0000:00:00.0"]

        assert "ahci" not in entry
        assert "ahci_error" not in entry

    def test_the_ahci_keys_are_real(self, fixture_pci_keys: set[str]) -> None:
        """RED control: `ahci` is a key the real reader really writes."""
        assert "ahci" in fixture_pci_keys


class TestPcieCapabilityAssembly:
    """`read_pci` reads config space and assembles the capability fields into the entry."""

    def test_a_pcie_capability_with_a_slot_is_assembled_into_the_entry(self, tmp_path: Path) -> None:
        device = _device(tmp_path, "0000:00:01.0", **{"class": "0x060400"})
        (device / "config").write_bytes(_pcie_config(slot_implemented=True, slot_number=16))

        entry = read_pci(tmp_path)["0000:00:01.0"]

        assert entry["slot_implemented"] is True
        assert entry["slot_number"] == 16
        assert "pcie_port_type" in entry

    def test_a_pcie_capability_with_no_slot_carries_no_slot_number(self, tmp_path: Path) -> None:
        device = _device(tmp_path, "0000:00:1c.0", **{"class": "0x060400"})
        (device / "config").write_bytes(_pcie_config(slot_implemented=False, slot_number=16))

        entry = read_pci(tmp_path)["0000:00:1c.0"]

        assert entry["slot_implemented"] is False
        assert "slot_number" not in entry

    def test_an_unreadable_config_file_assembles_nothing(self, tmp_path: Path) -> None:
        """No `config` file at all - the capability fields are all absent."""
        _device(tmp_path, "0000:00:1f.2", **{"class": "0x010601"})

        entry = read_pci(tmp_path)["0000:00:1f.2"]

        assert "pcie_port_type" not in entry
        assert "slot_implemented" not in entry
        assert "slot_number" not in entry

    def test_the_capability_keys_are_real(self, fixture_pci_keys: set[str]) -> None:
        """RED control: these are keys the real reader really writes."""
        assert {"pcie_port_type", "slot_implemented", "slot_number"} <= fixture_pci_keys


class TestAttributeDegradation:
    """An unreadable attribute is left out rather than aborting the device's reading."""

    def test_an_unreadable_attribute_is_left_out_but_its_siblings_still_read(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root reads a file whatever its mode, so the refusal cannot be planted")
        device = _device(tmp_path, "0000:00:00.0", **{"class": "0x060000", "vendor": "0x8086"})
        (device / "vendor").chmod(0o000)

        try:
            entry = read_pci(tmp_path)["0000:00:00.0"]
        finally:
            (device / "vendor").chmod(0o600)

        assert "vendor" not in entry
        assert entry["class"] == "0x060000"

    def test_a_missing_root_returns_an_empty_mapping_rather_than_raising(self, tmp_path: Path) -> None:
        assert read_pci(tmp_path / "does-not-exist") == {}


class TestClassesEnumeration:
    """`read_classes` reads each declared class's members and their own attributes."""

    def test_a_class_member_carries_its_declared_attributes_and_its_path(self, tmp_path: Path) -> None:
        member = tmp_path / "ata_link" / "link1"
        member.mkdir(parents=True)
        (member / "sata_spd").write_text("6.0 Gbps\n", encoding="utf-8")
        (member / "sata_spd_max").write_text("6.0 Gbps\n", encoding="utf-8")

        classes = read_classes(tmp_path)

        entry = classes["ata_link"]["link1"]
        assert entry["sata_spd"] == "6.0 Gbps"
        assert entry["sata_spd_max"] == "6.0 Gbps"
        assert entry["path"] == os.path.realpath(member)

    def test_an_attribute_the_member_never_published_is_left_out(self, tmp_path: Path) -> None:
        member = tmp_path / "ata_link" / "link1"
        member.mkdir(parents=True)
        # No sata_spd* files at all.

        entry = read_classes(tmp_path)["ata_link"]["link1"]

        assert "sata_spd" not in entry

    def test_a_class_with_no_base_directory_is_left_out_entirely(self, tmp_path: Path) -> None:
        # No "sas_phy" directory anywhere under tmp_path.
        classes = read_classes(tmp_path)

        assert "sas_phy" not in classes

    def test_two_classes_are_read_independently(self, tmp_path: Path) -> None:
        link = tmp_path / "ata_link" / "link1"
        link.mkdir(parents=True)
        (link / "sata_spd").write_text("6.0 Gbps\n", encoding="utf-8")
        host = tmp_path / "scsi_host" / "host0"
        host.mkdir(parents=True)
        (host / "proc_name").write_text("ahci\n", encoding="utf-8")

        classes = read_classes(tmp_path)

        assert classes["ata_link"]["link1"]["sata_spd"] == "6.0 Gbps"
        assert classes["scsi_host"]["host0"]["proc_name"] == "ahci"

    def test_the_shape_matches_what_a_real_capture_carries(
        self, tmp_path: Path, fixture_class_keys: dict[str, set[str]]
    ) -> None:
        member = tmp_path / "ata_link" / "link1"
        member.mkdir(parents=True)
        (member / "sata_spd").write_text("6.0 Gbps\n", encoding="utf-8")

        entry = read_classes(tmp_path)["ata_link"]["link1"]

        assert set(entry.keys()) <= (fixture_class_keys["ata_link"] | {"path"})


class TestShapeAgreesWithRealCaptures:
    """Every key a synthetic entry carries is one a real reader has really written."""

    def test_a_fully_populated_device_carries_only_keys_the_real_reader_uses(
        self, tmp_path: Path, fixture_pci_keys: set[str]
    ) -> None:
        device = _device(
            tmp_path,
            "0000:00:01.0",
            **{"class": "0x060400", "vendor": "0x8086", "device": "0x0e02"},
        )
        driver_dir = tmp_path / "drivers" / "pcieport"
        driver_dir.mkdir(parents=True)
        (device / "driver").symlink_to(driver_dir)
        (device / "0000:05:00.0").mkdir()
        (device / "config").write_bytes(_pcie_config(slot_implemented=True, slot_number=4))

        entry = read_pci(tmp_path)["0000:00:01.0"]

        assert set(entry.keys()) <= fixture_pci_keys
