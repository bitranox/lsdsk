"""What the Linux reader accepts as a disk, and how it classifies one.

A block device that is not backed by hardware has no transport and no SMART, so
it can never answer any of the questions this tool asks. It is still part of the
machine, so the reader keeps it and says so rather than dropping it: a device
that vanishes from an inventory is indistinguishable from one that is not there.

The kernel decides. A device with no physical parent resolves under
`/sys/devices/virtual`; a real one resolves under its PCI path. A name cannot
answer this, which is why an optical drive - named `sr0` and as physical as any
disk - is read as ordinary hardware.

The reader is importable anywhere, and most tests here stand a temporary tree
in for `/sys`, so those are `os_posix`: they need symlinks, which Windows
restricts, and they describe a layout no Windows machine has. The tree also
avoids colons in its path segments, which Windows rejects outright. The
Windows reader's own bus-transport decision needs no such tree, so that test
is `os_agnostic`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lsdsk.adapters.hw.linux import reader as linux_reader
from lsdsk.adapters.hw.linux.reader import read_block
from lsdsk.adapters.hw.windows.capture import bus_type_of
from lsdsk.domain.enums import BusType

_PCI_BLOCK = "devices/pci0000_00/0000_00_17_0/ata5/host4/block"


def _make_block_device(sysfs: Path, device_path: str, node: str, *, physical: bool) -> None:
    """Create one block device under `sysfs`, linked from /sys/block as the kernel does."""
    target = sysfs / device_path / node
    (target / "queue").mkdir(parents=True)
    (target / "size").write_text("1024\n", encoding="utf-8")
    (target / "queue" / "rotational").write_text("0\n", encoding="utf-8")
    if physical:
        (target / "device").mkdir()
        (target / "device" / "model").write_text("A Model\n", encoding="utf-8")
    block = sysfs / "block"
    block.mkdir(exist_ok=True)
    (block / node).symlink_to(target)


@pytest.fixture
def sysfs(tmp_path: Path) -> Path:
    """A sysfs tree holding real hardware and kernel-virtual block devices."""
    root = tmp_path / "sys"
    _make_block_device(root, _PCI_BLOCK, "sda", physical=True)
    _make_block_device(root, _PCI_BLOCK, "sr0", physical=True)
    _make_block_device(root, "devices/virtual/block", "zram0", physical=False)
    _make_block_device(root, "devices/virtual/block", "loop0", physical=False)
    return root


@pytest.mark.os_posix
class TestTheKernelDecidesWhatIsVirtual:
    """Placement in sysfs answers it; the device's name never does."""

    def test_a_real_disk_is_read(self, sysfs: Path) -> None:
        assert "sda" in read_block(sysfs / "block")

    def test_a_real_disk_is_not_marked_virtual(self, sysfs: Path) -> None:
        assert read_block(sysfs / "block")["sda"].get("virtual") is not True

    def test_a_kernel_virtual_device_is_read_rather_than_dropped(self, sysfs: Path) -> None:
        """Zram is RAM, so it reports no transport and no counters, forever.

        Dropping it made it disappear from a machine that has it, which reads
        as hardware that is not there rather than as hardware with nothing to
        say. It is kept and labelled instead.
        """
        assert "zram0" in read_block(sysfs / "block")

    def test_a_kernel_virtual_device_is_marked_virtual(self, sysfs: Path) -> None:
        assert read_block(sysfs / "block")["zram0"]["virtual"] is True

    def test_a_name_the_old_prefix_list_knew_is_decided_the_same_way(self, sysfs: Path) -> None:
        """`loop0` is virtual because of where it sits, not because of its name."""
        assert read_block(sysfs / "block")["loop0"]["virtual"] is True

    def test_an_optical_drive_is_real_hardware_despite_its_name(self, sysfs: Path) -> None:
        """`sr0` hangs off a real port and occupies it, so it is read as a disk.

        The prefix list excluded it by name. That hid a device which does take
        up an AHCI port, so the port accounting was short by one wherever a
        machine has one.
        """
        entry = read_block(sysfs / "block")["sr0"]
        assert entry.get("virtual") is not True


@pytest.mark.os_posix
class TestAnUnresolvableLinkIsNeverCalledVirtual:
    """A link that cannot be resolved says nothing either way, so it is real.

    Nothing in the machine ever forces `Path.resolve()` to raise for a plain
    dangling symlink on this platform - a missing target still resolves to an
    absolute path with `strict=False`, it simply is not under the virtual
    tree, so that alone can never prove the `except OSError` branch runs. The
    branch is reached only when resolving genuinely raises, which a
    permission-denied component or a name-resolution failure does on some
    systems, so it is exercised here by making the one call for this node
    raise `OSError` while every other `resolve()` call keeps working normally.
    Reading the answer as `False` (real hardware, not virtual) rather than
    `True` is the whole point: a refusal to resolve must never hide a real
    disk.
    """

    def test_a_link_that_cannot_be_resolved_is_not_classified_virtual(
        self, sysfs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        node = sysfs / "block" / "sda"
        real_resolve = Path.resolve

        def _resolve_or_raise(self: Path, *, strict: bool = False) -> Path:
            if self == node:
                raise OSError("simulated: link cannot be resolved")
            return real_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", _resolve_or_raise)
        entry = read_block(sysfs / "block")["sda"]
        assert entry.get("virtual") is not True


@pytest.mark.os_linux
def test_a_disk_whose_directory_cannot_be_listed_costs_only_its_own_partitions(sysfs: Path) -> None:
    """One unlistable disk must not abort the whole reading, nor read as partitionless."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root lists a directory whatever its mode, so the refusal cannot be planted")
    sda = sysfs / _PCI_BLOCK / "sda"
    sda.chmod(0o300)  # still searchable, so every attribute reads; only the listing fails
    try:
        block = read_block(sysfs / "block")
    finally:
        sda.chmod(0o700)
    assert "partitions" not in block["sda"], "an unlisted disk was recorded as having no partitions"
    assert block["sr0"]["partitions"] == {}


@pytest.mark.os_agnostic
def test_the_windows_reader_decides_nvme_through_the_shared_bus_conversion() -> None:
    """The choice between NVMe and ATA passthrough must not re-derive its own bus mapping.

    `read_disk` used to compare the raw transport string straight against
    `BusType.NVME`, which only ever worked because the string Windows reports
    for NVMe happens to be spelled the same as the enum's value. Routing the
    decision through `bus_type_of` - the same conversion `StorageDescriptor`
    validates through when a capture is replayed - means a transport spelling
    lsdsk does not already grade a rule for is resolved once, not twice with
    the two places free to disagree.
    """
    import ast

    from lsdsk.adapters.hw.windows import reader as windows_reader

    # Asserted on the AST rather than on the file's text: a substring check
    # passes for a mention in a docstring and fails on a reformat, and neither
    # answers whether read_disk itself calls the conversion.
    tree = ast.parse(Path(windows_reader.__file__).read_text(encoding="utf-8"))
    read_disk = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "read_disk")
    called = {
        node.func.id for node in ast.walk(read_disk) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "bus_type_of" in called, "read_disk does not route its bus decision through the shared conversion"

    # And the conversion the reader now shares with the capture model resolves
    # every transport name the same way replay would.
    assert bus_type_of("nvme") is BusType.NVME
    assert bus_type_of("scsi") is BusType.SAS
    assert bus_type_of("made-up-transport") is BusType.UNKNOWN


@pytest.mark.os_agnostic
def test_every_device_tree_walk_in_the_windows_reader_is_bounded() -> None:
    """A driver that returns a cycle must stop the walk, not the machine.

    `_ancestor_instances` walks up through `CM_Get_Parent` inside a `range`,
    and `_child_instances` walked the sibling chain under `while True:` with an
    unbounded list behind it: a device whose sibling chain loops hangs the scan
    and grows that list until the process dies. Neither loop can be driven from
    a test on this platform - the calls are cfgmgr32's - so the invariant is
    asserted on the source.

    What counts as bounded is the question, and a `while` is not automatically
    the unbounded shape: the two SetupAPI enumerations advance an `index` OF
    THEIR OWN and hand it to the call in the condition, so each turn asks about
    a different device and the enumeration ends itself. The shape that cannot
    end is a loop whose condition rests on nothing the body advances - a bare
    `while True`, or a cursor read back from the driver every turn.
    """
    import ast

    from lsdsk.adapters.hw.windows import reader as windows_reader

    def unbounded_loops(source: str) -> set[str]:
        """The functions in `source` holding a loop nothing in the body counts down."""
        found: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            for loop in ast.walk(node):
                if isinstance(loop, ast.While) and not _counts_its_own_turns(loop):
                    found.add(node.name)
        return found

    def _counts_its_own_turns(loop: ast.While) -> bool:
        """Whether the loop's own body advances a name its condition reads."""
        asked = {name.id for name in ast.walk(loop.test) if isinstance(name, ast.Name)}
        advanced = {
            step.target.id
            for step in ast.walk(loop)
            if isinstance(step, ast.AugAssign) and isinstance(step.target, ast.Name)
        }
        return bool(asked & advanced)

    source = Path(windows_reader.__file__).read_text(encoding="utf-8")
    assert unbounded_loops(source) == set(), "a device-tree walk can run forever"

    # Not vacuous, in both directions: it names the shape that cannot end, and
    # leaves the enumeration that ends itself alone.
    cycle = "class T:\n    def _child_instances(self):\n        while True:\n            pass\n"
    assert unbounded_loops(cycle) == {"_child_instances"}
    enumeration = "class T:\n    def walk(self):\n        i = 0\n        while self.next(i):\n            i += 1\n"
    assert unbounded_loops(enumeration) == set()


def _sas_phy_with_port_link(root: Path) -> Path:
    """A ``sas_phy`` class member whose ``device/port`` link leads to ``port-0:0``; returns its ``device`` dir."""
    (root / "ports" / "port-0:0").mkdir(parents=True)
    device = root / "sas_phy" / "phy-0:0" / "device"
    device.mkdir(parents=True)
    (device / "port").symlink_to(root / "ports" / "port-0:0")
    return device


@pytest.mark.os_posix
def test_a_phys_port_link_is_read_as_the_name_of_the_port_it_leads_to(tmp_path: Path) -> None:
    """The control for the unreadable case below: the same tree, readable, names its port."""
    _sas_phy_with_port_link(tmp_path)

    assert linux_reader.read_classes(tmp_path)["sas_phy"]["phy-0:0"]["port"] == "port-0:0"


@pytest.mark.os_posix
def test_a_phys_port_link_behind_an_unreadable_directory_is_no_port_rather_than_an_error(tmp_path: Path) -> None:
    """EACCES is re-raised by ``Path.is_symlink`` before Python 3.14; the reader reads it as no link."""
    device = _sas_phy_with_port_link(tmp_path)
    device.chmod(0o000)
    try:
        try:
            (device / "port").lstat()
        except PermissionError:
            pass
        else:
            pytest.skip("this user can look inside a mode-000 directory (root)")
        classes = linux_reader.read_classes(tmp_path)
    finally:
        device.chmod(0o755)

    assert "port" not in classes["sas_phy"]["phy-0:0"]


@pytest.mark.os_posix
def test_a_cpuinfo_that_cannot_be_opened_gives_no_hypervisor_flag_rather_than_an_error(tmp_path: Path) -> None:
    """A directory where ``cpuinfo`` should be opens to EISDIR, and the evidence says the flag is absent."""
    (tmp_path / "cpuinfo").mkdir()

    assert linux_reader.read_environment(proc=tmp_path)["hypervisor_flag"] is False


@pytest.mark.os_posix
def test_a_cpuinfo_naming_the_hypervisor_flag_is_read(tmp_path: Path) -> None:
    """The control for the case above: the same reader, given a file, sees the flag."""
    (tmp_path / "cpuinfo").write_text("flags\t\t: fpu hypervisor lm\n", encoding="utf-8")

    assert linux_reader.read_environment(proc=tmp_path)["hypervisor_flag"] is True


@pytest.mark.os_posix
def test_a_block_device_whose_directory_cannot_be_searched_costs_only_its_own_reading(sysfs: Path) -> None:
    """Before Python 3.14 ``Path.exists`` re-raises EACCES, which aborted the whole scan."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root searches a directory whatever its mode, so the refusal cannot be planted")
    sda = sysfs / _PCI_BLOCK / "sda"
    sda.chmod(0o000)
    try:
        block = read_block(sysfs / "block")
    finally:
        sda.chmod(0o700)
    assert "device" not in block["sda"], "an unsearchable disk was recorded as carrying a device directory"
    assert block["sr0"]["device"]["model"] == "A Model"


@pytest.mark.os_posix
def test_a_pci_device_whose_directory_cannot_be_searched_costs_only_its_own_reading(tmp_path: Path) -> None:
    """``Path.is_symlink`` on its ``driver`` link re-raised EACCES before Python 3.14."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root searches a directory whatever its mode, so the refusal cannot be planted")
    root = tmp_path / "devices"
    for address in ("0000_03_00.0", "0000_04_00.0"):
        (root / address).mkdir(parents=True)
        (root / address / "vendor").write_text("0x1000\n", encoding="utf-8")
    locked = root / "0000_03_00.0"
    locked.chmod(0o000)
    try:
        devices = linux_reader.read_pci(root)
    finally:
        locked.chmod(0o700)
    assert devices["0000_04_00.0"]["vendor"] == "0x1000"
    assert "driver" not in devices["0000_03_00.0"]


@pytest.mark.os_posix
def test_the_kernels_placement_decides_even_when_the_names_say_the_opposite(tmp_path: Path) -> None:
    """A virtual-tree device named like a disk is virtual; a PCI-backed one named like a RAM disk is real.

    ``zram0`` and ``loop0`` are classified right by a prefix list as well, so
    they cannot show the kernel is being asked. These two are named so that a
    name list answers both wrongly.
    """
    root = tmp_path / "sys"
    _make_block_device(root, "devices/virtual/block", "sdq", physical=False)
    _make_block_device(root, _PCI_BLOCK, "loop5", physical=True)
    _make_block_device(root, _PCI_BLOCK, "md0", physical=True)

    block = read_block(root / "block")

    assert block["sdq"]["virtual"] is True
    assert block["loop5"].get("virtual") is not True
    assert block["md0"].get("virtual") is not True
