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
        """zram is RAM, so it reports no transport and no counters, forever.

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
