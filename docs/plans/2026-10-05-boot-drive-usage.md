# Boot Drive and "Used By" Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use bitranox:process-agents-subagent-driven-development (recommended) or bitranox:process-plan-executor to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every disk says whether the machine boots from it and what uses it (a mountpoint, a drive letter, swap, or the ZFS pool / LVM group / md array / crypt mapping it belongs to), in the disks table, the detail panel and the JSON payload, on Linux and Windows.

**Architecture:** Follows `docs/adr/0004-boot-drive-and-what-uses-a-disk.md`. Each platform's impure reader records the raw sources in the capture (new OPTIONAL fields, so every committed capture still parses); a pure resolver per platform turns them into a `DiskUsage` per disk; the render layer formats one cell from it. Decoding stays on the pure side of the reader/builder line.

**Tech Stack:** Python 3.11+, pydantic 2 (capture and domain models), ctypes (Windows), rich / textual (render), pytest.

## Global Constraints

- No subprocess, no network, on either platform. Linux reads must work unprivileged.
- Three cell states, never two: `-` (`theme.NOT_READ`) = not read; `not mounted` = read, nothing in use; anything else = content. A capture without the new fields renders `-`.
- "not mounted" is claimed only when EVERY source that could have named the disk was read (Linux: the udev database was present; Windows: no volume failed to open). Otherwise a disk with nothing resolved is `-`.
- A container (`Environment.CONTAINER`) resolves every disk to `None` (`-`): the host's use of its disks is invisible from inside.
- Partition-level filesystem signatures beat a whole-disk one; a whole-disk signature is consulted only when no partition of that disk carries one.
- ZFS shows the POOL (`zfs:rpool`), never its datasets.
- Boot = Linux: disks under `/`, plus any disk under a mounted `/boot` or `/boot/efi`; Windows: the disk(s) of the Windows directory's volume, plus the disk(s) of the EFI system partition (GPT type `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`).
- Cell text: `boot ` prefix when boot, then uses joined by `, `. MOUNT/LETTER -> the mount paths; SWAP -> `swap`; ZFS -> `zfs:<pool>`; LVM/MD/CRYPT/STACK -> `<kind>:<name>` plus ` -> <mounts>` when it has any.
- NO `display.used_by_width` key (ADR 0004 is amended in Task 7): the column is flexible and clipped with `>` like `model`.
- Repo rules: `make test` is the gate; never `git commit -m` (write the message to a file, `git commit -F`); no Claude/AI attribution in commits; ASCII punctuation only in docs; every new public module states `__all__`; `tests/*.py` needs no `# noqa: PLC0415`.
- `skills/lsdsk/SKILL.md` changes ONLY through `bitranox:meta-skill-writer`.
- Every English top-level doc with a German twin in `de/` needs the twin translated and `scripts/translation_manifest.py --refresh <doc>`.

---

### Task 1: Measure the two unmeasured Windows readings

No repo code. Decides whether Task 5 records a refusal.

**Files:**
- Create (scratch, NOT in the repo): `<scratchpad>/winvolprobe2.py`

**STOP conditions:**
- An UNELEVATED process cannot open ANY volume handle (every volume errors with 5): stop and report - Task 5's error rule must then become a recorded refusal, which is a design change for the user.
- `IOCTL_DISK_GET_PARTITION_INFO_EX` fails on a volume handle even elevated: stop and report.

- [ ] **Step 1: Extend the feasibility probe** - start from the probe used for ADR 0004 (volume enumeration + `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`) and add, per volume, `IOCTL_DISK_GET_PARTITION_INFO_EX` (`0x00070048`) on the same zero-access handle, printing `PartitionStyle` (bytes 0-3, little-endian) and the GPT `PartitionType` GUID (`uuid.UUID(bytes_le=buf[32:48])`) from a 144-byte buffer.
- [ ] **Step 2: Run it elevated** on vm-pydev-win: the FAT32 volume must report GPT style (1) and the ESP GUID.
- [ ] **Step 3: Run it unelevated** on vm-pydev-win: launch it through a scheduled task created with `/RL LIMITED` writing its JSON to a file read back over SSH (the SSH session there is already elevated). Record per volume whether open, extents and partition info succeed.
- [ ] **Step 4: Record the result** as one paragraph appended to ADR 0004's Consequences (in Task 7) - write it into `<scratchpad>/task1-result.txt` now.

---

### Task 2: Domain model for disk usage

**Files:**
- Modify: `src/lsdsk/domain/enums.py` (add `UseKind`, export it)
- Modify: `src/lsdsk/domain/models.py` (add `DiskUse`, `DiskUsage`, `Disk.usage`)
- Test: `tests/test_disk_usage_model.py`

**Interfaces:**
- Produces:
  - `UseKind(StrEnum)`: `MOUNT="mount"`, `LETTER="letter"`, `SWAP="swap"`, `ZFS="zfs"`, `LVM="lvm"`, `MD="md"`, `CRYPT="crypt"`, `STACK="stack"`.
  - `DiskUse(DomainModel, frozen=True)`: `kind: UseKind`, `name: DeviceText = ""`, `mounts: tuple[DeviceText, ...] = ()`.
  - `DiskUsage(DomainModel, frozen=True)`: `boot: bool = False`, `uses: tuple[DiskUse, ...] = ()`.
  - `Disk.usage: DiskUsage | None = None` (``None`` = not read).

**Out of scope:** `adapters/render/*` - formatting is Task 6; the domain carries structure only.

**STOP conditions:** `DeviceText` / `DomainModel` are not where `models.py` imports them from today.

- [ ] **Step 1: Write the failing test**

```python
"""What a disk's usage can say, and what an absent one means."""

from __future__ import annotations

import pytest

from lsdsk.domain.enums import UseKind
from lsdsk.domain.models import Disk, DiskUsage, DiskUse


@pytest.mark.os_agnostic
def test_a_disk_built_without_a_usage_reading_says_it_was_not_read() -> None:
    assert Disk(node="sda", path="/dev/sda", model="m").usage is None


@pytest.mark.os_agnostic
def test_a_usage_carries_the_boot_mark_and_its_uses_in_order() -> None:
    usage = DiskUsage(
        boot=True,
        uses=(DiskUse(kind=UseKind.ZFS, name="rpool"), DiskUse(kind=UseKind.SWAP)),
    )
    disk = Disk(node="sde", path="/dev/sde", model="m", usage=usage)
    assert disk.usage is not None
    assert disk.usage.boot is True
    assert [use.kind for use in disk.usage.uses] == [UseKind.ZFS, UseKind.SWAP]


@pytest.mark.os_agnostic
def test_a_use_refuses_a_field_it_does_not_declare() -> None:
    with pytest.raises(ValueError, match="mountpoint"):
        DiskUse(kind=UseKind.MOUNT, mountpoint="/")  # type: ignore[call-arg]


@pytest.mark.os_agnostic
def test_the_usage_reaches_the_json_payload_by_name() -> None:
    usage = DiskUsage(uses=(DiskUse(kind=UseKind.LETTER, mounts=("C:\\",)),))
    dumped = Disk(node="PhysicalDrive0", path="p", model="m", usage=usage).model_dump(mode="json")
    assert dumped["usage"] == {"boot": False, "uses": [{"kind": "letter", "name": "", "mounts": ["C:\\"]}]}
```

- [ ] **Step 2: Run it** - `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/test_disk_usage_model.py -q -p no:cacheprovider` - expect ImportError on `UseKind`.

- [ ] **Step 3: Implement.** In `enums.py`, beside the other StrEnums (Google docstring, `Attributes:` per member, a doctest like its neighbours):

```python
class UseKind(StrEnum):
    """What one use of a disk is.

    Attributes:
        MOUNT: A filesystem mounted straight from the disk or a partition of it.
        LETTER: A Windows volume path, a drive letter or a folder mount.
        SWAP: Active swap.
        ZFS: A member of a ZFS pool, named by the pool.
        LVM: A physical volume of an LVM volume group, named by the group.
        MD: A member of a Linux software RAID array, named by its node.
        CRYPT: Under a dm-crypt mapping, named by the mapping.
        STACK: Under any other device-mapper device, named by it.

    Example:
        >>> UseKind.ZFS.value
        'zfs'
    """

    MOUNT = "mount"
    LETTER = "letter"
    SWAP = "swap"
    ZFS = "zfs"
    LVM = "lvm"
    MD = "md"
    CRYPT = "crypt"
    STACK = "stack"
```

In `models.py`, above `class Disk`:

```python
class DiskUse(DomainModel, frozen=True):
    """One thing a disk is used for.

    Attributes:
        kind: What the use is.
        name: The pool, volume group, array or mapping it belongs to; empty for
            a mount, a letter or swap, which the mounts already name.
        mounts: Where it is mounted: mountpoints on Linux, volume paths on
            Windows. A ZFS use carries none, because a pool's datasets are
            unbounded in number and say nothing about the disk.

    Example:
        >>> DiskUse(kind=UseKind.LVM, name="vg0", mounts=("/var",)).name
        'vg0'
    """

    kind: UseKind
    name: DeviceText = ""
    mounts: tuple[DeviceText, ...] = ()


class DiskUsage(DomainModel, frozen=True):
    """Whether the machine boots from a disk, and what uses it.

    An inventory that could not read this leaves ``Disk.usage`` at ``None``;
    an empty ``uses`` is a reading that found nothing, which is a different
    claim and is only made when every source that could have named the disk
    was read.

    Attributes:
        boot: Whether the running system or a boot partition is on it.
        uses: Everything found on it, in the order it was found.

    Example:
        >>> DiskUsage().uses
        ()
    """

    boot: bool = False
    uses: tuple[DiskUse, ...] = ()
```

Add to `Disk`'s docstring `Attributes:` a line `usage: Whether the machine boots from it and what uses it, or ``None`` where that could not be read.` and the field `usage: DiskUsage | None = None` after `readings_refused`. Import `UseKind` in `models.py`; add `DiskUse`, `DiskUsage` to `models.__all__` and `UseKind` to `enums.__all__`.

- [ ] **Step 4: Run** the new file, then `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests -q -p no:cacheprovider -x -k "skill or envelope or json or domain"` - expect `test_the_skill_enumerates_the_fields_a_disk_really_carries` to FAIL (payload now has `usage`, the skill does not). That failure is expected and is closed in Task 8; note it, do not touch SKILL.md here.
- [ ] **Step 5: Commit** - message file: `feat(domain): a disk's usage - boot mark and what uses it`.

---

### Task 3: Linux reader and capture fields

**Files:**
- Create: `src/lsdsk/adapters/hw/linux/mounts.py` (impure, but every function takes its root path, so it is tested with `tmp_path` and stays in coverage)
- Modify: `src/lsdsk/adapters/hw/linux/reader.py` (`read_block` adds `dev`, `holders`, `partitions`; `read_system` adds four keys)
- Modify: `src/lsdsk/adapters/hw/linux/capture.py` (new models + fields)
- Test: `tests/test_linux_mounts_reader.py`

**Interfaces:**
- Produces (reader, plain dicts shaped for the capture):
  - `read_mounts(path: Path = Path("/proc/self/mountinfo")) -> list[dict[str, str]] | None` - `None` when unreadable.
  - `read_swaps(path: Path = Path("/proc/swaps")) -> list[dict[str, str]]`
  - `read_partitions(node: Path) -> dict[str, dict[str, Any]]`
  - `read_stacked(names: Iterable[str], root: Path = Path("/sys/block")) -> dict[str, dict[str, Any]]`
  - `read_signatures(devnums: Iterable[str], root: Path = Path("/run/udev/data")) -> dict[str, dict[str, str]] | None` - `None` when `root` is not a directory.
- Produces (capture, `capture.py`):
  - `MountEntry`: `dev: DeviceText`, `mountpoint: DeviceText`, `fstype: DeviceText = ""`, `source: DeviceText = ""`, `source_dev: DeviceText | None = None`
  - `SwapEntry`: `path: DeviceText`, `dev: DeviceText | None = None`
  - `PartitionEntry`: `dev: DeviceText | None = None`, `holders: Entries[DeviceText] = ()`
  - `StackedEntry`: `dev: DeviceText | None = None`, `dm_name: DeviceText | None = None`, `dm_uuid: DeviceText | None = None`, `holders: Entries[DeviceText] = ()`
  - `FilesystemSignature`: `fs_type: DeviceText | None = None`, `fs_label: DeviceText | None = None`
  - `BlockEntry` gains `dev: DeviceText | None = None`, `holders: Entries[DeviceText] = ()`, `partitions: EntryMap[DeviceText, PartitionEntry] | None = None`
  - `LinuxCapture` gains `mounts: Entries[MountEntry] | None = None`, `swaps: Entries[SwapEntry] = ()`, `stacked: EntryMap[DeviceText, StackedEntry] = Field(default_factory=dict[DeviceText, StackedEntry])`, `signatures: EntryMap[DeviceText, FilesystemSignature] | None = None` (keyed by `maj:min`).

**Out of scope:**
- `tests/fixtures/hw/*.json` - real captures; never add invented fields to them.
- `adapters/hw/snapshot.py` - parsing goes through `parse_capture` unchanged; optional fields need nothing there.

**STOP conditions:**
- `Entries[...] | None` or `EntryMap[...] | None` is refused by pydantic or pyright: stop and report rather than loosening `Entries`.
- A size bound in `capture.py` (`Entries` length cap) is smaller than a real host's mount count (66 ZFS mounts measured; docker hosts run to thousands): report the cap before raising it.

- [ ] **Step 1: Write the failing tests** (`tests/test_linux_mounts_reader.py`), building fake trees under `tmp_path`:

```python
"""The Linux sources that say what uses a disk, read from fake trees."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lsdsk.adapters.hw.linux import mounts

MOUNTINFO = (
    "22 1 0:28 / / rw,relatime shared:1 - zfs rpool/ROOT/pve-1 rw,xattr\n"
    "30 22 8:2 / /boot\\040efi rw - vfat /dev/sda2 rw\n"
    "31 22 0:40 / /srv rw - btrfs /dev/sdb1 rw\n"
)


@pytest.mark.os_agnostic
def test_mountinfo_rows_keep_device_mountpoint_type_and_source(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(MOUNTINFO, encoding="utf-8")
    rows = mounts.read_mounts(path)
    assert rows is not None
    assert rows[0] == {"dev": "0:28", "mountpoint": "/", "fstype": "zfs", "source": "rpool/ROOT/pve-1"}
    # The kernel octal-escapes a space; the reader hands back the real path.
    assert rows[1]["mountpoint"] == "/boot efi"


@pytest.mark.os_agnostic
def test_an_unreadable_mountinfo_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    assert mounts.read_mounts(tmp_path / "absent") is None


@pytest.mark.os_agnostic
def test_swap_rows_skip_the_header(tmp_path: Path) -> None:
    path = tmp_path / "swaps"
    path.write_text(
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/nvme4n1p1   partition\t8388604\t\t0\t\t-2\n",
        encoding="utf-8",
    )
    assert [row["path"] for row in mounts.read_swaps(path)] == ["/dev/nvme4n1p1"]


@pytest.mark.os_agnostic
def test_partitions_carry_their_device_number_and_holders(tmp_path: Path) -> None:
    disk = tmp_path / "sda"
    part = disk / "sda2"
    (part / "holders" / "dm-0").mkdir(parents=True)
    (part / "partition").write_text("2\n", encoding="utf-8")
    (part / "dev").write_text("8:2\n", encoding="utf-8")
    (disk / "queue").mkdir()  # a non-partition child must not be listed
    assert mounts.read_partitions(disk) == {"sda2": {"dev": "8:2", "holders": ["dm-0"]}}


@pytest.mark.os_agnostic
def test_a_stacked_device_names_its_mapping_and_what_sits_on_it(tmp_path: Path) -> None:
    dm = tmp_path / "dm-0"
    (dm / "dm").mkdir(parents=True)
    (dm / "holders" / "dm-1").mkdir(parents=True)
    (dm / "dev").write_text("253:0\n", encoding="utf-8")
    (dm / "dm" / "name").write_text("cryptroot\n", encoding="utf-8")
    (dm / "dm" / "uuid").write_text("CRYPT-LUKS2-abc-cryptroot\n", encoding="utf-8")
    found = mounts.read_stacked(["dm-0"], tmp_path)
    assert found == {
        "dm-0": {"dev": "253:0", "dm_name": "cryptroot", "dm_uuid": "CRYPT-LUKS2-abc-cryptroot", "holders": ["dm-1"]}
    }


@pytest.mark.os_agnostic
def test_udev_signatures_keep_only_the_filesystem_type_and_label(tmp_path: Path) -> None:
    (tmp_path / "b8:67").write_text(
        "S:disk/by-id/x\nE:ID_FS_TYPE=zfs_member\nE:ID_FS_LABEL=rpool\nE:ID_SERIAL=SECRET\n", encoding="utf-8"
    )
    assert mounts.read_signatures(["8:67", "8:1"], tmp_path) == {"8:67": {"fs_type": "zfs_member", "fs_label": "rpool"}}


@pytest.mark.os_agnostic
def test_an_absent_udev_database_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    assert mounts.read_signatures(["8:1"], tmp_path / "absent") is None


@pytest.mark.os_linux
def test_a_dev_source_is_resolved_to_its_device_number(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text("31 22 0:40 / /x rw - tmpfs /dev/null rw\n", encoding="utf-8")
    rows = mounts.read_mounts(path)
    assert rows is not None
    rdev = os.stat("/dev/null").st_rdev
    assert rows[0]["source_dev"] == f"{os.major(rdev)}:{os.minor(rdev)}"
```

- [ ] **Step 2: Run** `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/test_linux_mounts_reader.py -q -p no:cacheprovider` - expect ImportError.

- [ ] **Step 3: Implement `mounts.py`.** Read files through `adapters/textfile.py`'s bounded reader (check its exported name first; add a module constant `MAX_MOUNTINFO_BYTES = 8 * 1024 * 1024` with a WHY comment: a container host lists thousands of mounts). Contents:

```python
"""Read the Linux sources that say what uses each disk.

Every function takes the root it reads from, so tests drive it over a fake
tree and it stays in coverage, unlike the ioctl transport beside it. Nothing
here needs privilege: mountinfo, /proc/swaps, sysfs and the udev database are
all world-readable.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The kernel writes a space, tab, newline or backslash in a mount path as a
# three-digit octal escape, so "/boot efi" arrives as "/boot\040efi".
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
# The two udev properties this tool reads. The database also holds serials and
# paths; keeping only these keeps a capture's new fields free of identifiers.
_SIGNATURE_KEYS = {"ID_FS_TYPE": "fs_type", "ID_FS_LABEL": "fs_label"}


def _unescape(text: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), text)


def _device_number_of(path: str) -> str | None:
    """The ``maj:min`` of a device node, or None when it is not one."""
    try:
        rdev = os.stat(path).st_rdev
    except OSError:
        return None
    return f"{os.major(rdev)}:{os.minor(rdev)}" if rdev else None
```

then `read_mounts` (split each line on `" - "`; fields left: index 2 = `dev`, index 4 = `mountpoint`; right: index 0 = `fstype`, index 1 = `source`; add `source_dev` only when `source.startswith("/dev/")` and it resolves; skip a malformed line rather than raising), `read_swaps` (skip line 0; first whitespace field is the path; add `dev` via `_device_number_of`), `read_partitions` (children of `node` that contain a `partition` file; `dev` from `<child>/dev`; `holders` sorted names of `<child>/holders/*`), `read_stacked` (for each name: `dev`, `dm/name`, `dm/uuid` when present, `holders`; follow holders transitively so a crypt-under-LVM chain is captured in one call - keep a `seen` set), `read_signatures` (`None` if `root` is not a dir; for each devnum read `b<devnum>`, keep `E:` lines whose key is in `_SIGNATURE_KEYS`; omit a devnum with neither). Each function has a Google docstring with Args/Returns; `__all__` lists the five readers.

- [ ] **Step 4: Wire the reader.** In `reader.read_block`, for a non-virtual node: `entry["dev"] = _read_attribute(node / "dev")`, `entry["holders"] = sorted(p.name for p in _entries(node / "holders"))` (check `_entries` tolerates a missing dir), `entry["partitions"] = mounts.read_partitions(node)`. In `read_system`, after `block = read_block()`:

```python
    holders = {name for entry in block.values() for name in entry.get("holders", ())}
    holders |= {
        name for entry in block.values() for part in entry.get("partitions", {}).values() for name in part["holders"]
    }
    devnums = [entry["dev"] for entry in block.values() if entry.get("dev")]
    devnums += [part["dev"] for entry in block.values() for part in entry.get("partitions", {}).values() if part.get("dev")]
```

and add to the returned dict: `"mounts": mounts.read_mounts()`, `"swaps": mounts.read_swaps()`, `"stacked": mounts.read_stacked(sorted(holders))`, `"signatures": mounts.read_signatures(devnums)`.

- [ ] **Step 5: Add the capture models** listed under Interfaces to `capture.py`, each with a docstring whose `Attributes:` names every field and says what `None` means, and add them to `__all__`. Add a test to `tests/test_linux_mounts_reader.py` that `LinuxCapture.model_validate` accepts a minimal reading carrying all four new keys AND that every committed `tests/fixtures/hw/linux-*.json` still validates with `mounts is None` and `signatures is None`.
- [ ] **Step 6: Run** the file, then `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests -q -p no:cacheprovider -k "capture or snapshot or fixture or reader"`; then `.venv/bin/pyright --pythonpath .venv/bin/python src/lsdsk/adapters/hw/linux` - 0 errors.
- [ ] **Step 7: Live check** on this host: `env -u VIRTUAL_ENV .venv/bin/lsdsk snapshot -o <scratchpad>/live.json` and confirm `mounts` is a list, `signatures` is `null` here (no udev in this container) and the file validates through `--replay`.
- [ ] **Step 8: Commit** - `feat(linux): read mounts, swap, partitions, stacked devices and udev signatures`.

---

### Task 4: Linux usage resolver

**Files:**
- Create: `src/lsdsk/adapters/hw/linux/usage.py` (pure)
- Modify: `src/lsdsk/adapters/hw/linux/builder.py` (`build_inventory` attaches usage)
- Test: `tests/test_linux_usage.py`

**Interfaces:**
- Consumes: Task 2's `DiskUse`, `DiskUsage`, `UseKind`; Task 3's capture fields.
- Produces: `resolve_usage(capture: LinuxCapture, environment: Environment) -> dict[str, DiskUsage | None]`, keyed by node name, one entry per NON-virtual block device.

**Out of scope:** `build_virtual_disks` - kernel-virtual devices keep `usage=None`; a zvol's usage is the pool's business and is not asked for.

**STOP conditions:** `environment` as `build_inventory` computes it is not an `Environment` member (check `classify`'s return).

- [ ] **Step 1: Write the failing tests.** Build captures as dicts and validate them through `LinuxCapture.model_validate`, so the test exercises the real model. Helper and cases:

```python
"""What a Linux disk is used for, resolved from hand-built captures."""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.usage import resolve_usage
from lsdsk.domain.enums import Environment, UseKind


def _capture(block: dict[str, Any], **extra: Any) -> LinuxCapture:
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "linux",
        "hostname": "h",
        "kernel": "6.1",
        "pci": {},
        "block": block,
        "mounts": [],
        "swaps": [],
        "stacked": {},
        "signatures": {},
    }
    reading.update(extra)
    return LinuxCapture.model_validate(reading)


def _disk(dev: str, partitions: dict[str, Any] | None = None, holders: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"dev": dev, "holders": list(holders), "partitions": partitions or {}}


def _mount(dev: str, mountpoint: str, fstype: str = "ext4", source: str = "") -> dict[str, str]:
    return {"dev": dev, "mountpoint": mountpoint, "fstype": fstype, "source": source}


BARE_METAL = Environment.BARE_METAL
```

Tests (each one assertion-focused, `@pytest.mark.os_agnostic`):
1. `test_a_plain_root_partition_marks_its_disk_boot_and_names_the_mount` - sda with sda1 `8:1` mounted `/` -> `boot=True`, uses `[MOUNT ("/",)]`.
2. `test_a_separate_efi_disk_is_boot_too` - sdb1 mounted `/boot/efi` -> sdb `boot=True`; sdc with nothing -> `DiskUsage(boot=False, uses=())`.
3. `test_a_zfs_mirror_marks_every_member_and_shows_the_pool_not_its_datasets` - mounts `/` zfs source `rpool/ROOT/pve-1` dev `0:28` plus `/rpool/data` zfs; sde3 `8:67` and sdf3 `8:83` signatures `zfs_member/rpool` -> both `boot=True`, uses `[ZFS name="rpool" mounts=()]`.
4. `test_a_whole_disk_signature_loses_to_the_partition_one` - sdb whole-disk dev `8:16` signature `zfs_member/rpool` AND sdb3 `8:19` signature `zfs_member/rpool` -> exactly ONE ZFS use; and control: a disk whose ONLY signature is whole-disk `ddf_raid_member` with a zfs partition -> no `ddf` anywhere.
5. `test_lvm_under_a_partition_names_the_group_and_its_mounts` - sda2 holders `dm-0`; stacked `dm-0` dev `253:0`, dm_name `vg0-var`, dm_uuid `LVM-...`; mount `253:0 /var` -> `LVM name="vg0" mounts=("/var",)`. Plus an escaped name `my--vg-lv` -> group `my-vg`.
6. `test_crypt_over_lvm_reports_the_first_layer_with_every_mount_below_it` - sda2 -> dm-0 (CRYPT, `cryptroot`) -> dm-1 (LVM `vg0-root`) mounted `/` -> `CRYPT name="cryptroot" mounts=("/",)`, `boot=True`.
7. `test_an_md_member_names_its_array` - sda1 holders `md0`; stacked `md0` (no dm) mounted `/srv` -> `MD name="md0" mounts=("/srv",)`.
8. `test_swap_on_a_partition_reads_swap` - swaps `[{"path": "/dev/nvme4n1p1", "dev": "259:13"}]`, nvme4n1p1 dev `259:13` -> `[SWAP]`.
9. `test_btrfs_is_found_through_its_source_device` - mount dev `0:40` (anonymous) `/srv` btrfs source `/dev/sdb1` `source_dev` `8:17`, sdb1 dev `8:17` -> `MOUNT ("/srv",)`.
10. `test_a_container_reads_every_disk_as_not_read` - same capture as case 1 with `Environment.CONTAINER` -> `{"sda": None}`.
11. `test_a_capture_without_the_new_fields_reads_not_read` - `mounts=None` -> every disk `None`.
12. `test_without_udev_a_disk_with_nothing_found_is_not_read_but_a_found_one_stays` - `signatures=None`; sda1 mounted `/`, sdb nothing -> sda resolved, sdb `None`.
13. `test_a_disk_with_no_partitions_is_its_own_leaf` - sdc dev `8:32`, no partitions, mounted `/data` -> `MOUNT ("/data",)`.
14. `test_one_mount_seen_twice_is_listed_once` - two mountinfo rows `/var` for the same dev (bind) -> `mounts=("/var",)`.

- [ ] **Step 2: Run** `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/test_linux_usage.py -q -p no:cacheprovider` - ImportError.

- [ ] **Step 3: Implement `usage.py`.** Small functions (complexity <= 10 each):

```python
"""Resolve what uses each Linux disk, from what the reader recorded.

Pure: a capture in, one ``DiskUsage`` per disk out. See ADR 0004 for the
sources and why each rule is what it is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from ....domain.enums import Environment, UseKind
from ....domain.models import DiskUsage, DiskUse
from .capture import BlockEntry, LinuxCapture, MountEntry, StackedEntry

#: Mountpoints whose disk the machine boots from.
BOOT_MOUNTS = frozenset({"/", "/boot", "/boot/efi"})
_ZFS_MEMBER = "zfs_member"
# LVM writes a dash inside a group or volume name as two, so the single dash is
# the separator between them.
_LVM_SEPARATOR = re.compile(r"(?<!-)-(?!-)")
```

Functions:
- `_mounts_by_dev(capture) -> dict[str, list[str]]` - keyed by both `dev` and `source_dev`, mountpoints deduplicated in order.
- `_boot_pools(capture) -> set[str]` - `source.split("/")[0]` of every mount in `BOOT_MOUNTS` with `fstype == "zfs"`.
- `_closure(name, stacked) -> list[str]` - transitive holders, cycle-safe.
- `_stack_use(name, stacked, mounts_by_dev, swap_devs) -> DiskUse` - kind from `dm_uuid` prefix (`LVM-` -> LVM with group parsed by `_LVM_SEPARATOR` then `--` -> `-`; `CRYPT-` -> CRYPT with `dm_name`), `md` prefix with no dm -> MD with the node name, else STACK with `dm_name or name`; mounts = every mount of every device in `[name, *closure]` (plus `"swap"` if one is swap).
- `_leaf_uses(dev, holders, signature, ...) -> list[DiskUse]` - direct mounts -> MOUNT; dev in swap devs -> SWAP; `signature.fs_type == "zfs_member"` and a label -> ZFS (name=label); each direct holder -> `_stack_use`.
- `_merge(uses) -> tuple[DiskUse, ...]` - one use per `(kind, name)`; MOUNT/LETTER merged into one use with all mounts; order of first appearance.
- `_disk_usage(block, capture, context) -> DiskUsage | None` - leaves = partitions (or the disk itself when it has none); whole-disk signature only when no partition has one; `boot` if any use's mounts meet `BOOT_MOUNTS` or a ZFS use's name is in boot pools; `None` when nothing found and `capture.signatures is None`.
- `resolve_usage(capture, environment)` - `None` for all when `environment is Environment.CONTAINER` or `capture.mounts is None`; otherwise per non-virtual block.

Use a small frozen NamedTuple `_Sources(mounts_by_dev, swap_devs, boot_pools)` built once rather than threading three parameters (the repo's six-parameter census test refuses wide signatures).

- [ ] **Step 4: Wire** - in `builder.build_inventory`, after `environment, detail = classify(...)`:

```python
    usage = resolve_usage(capture, environment)
    disks = tuple(disk.with_changes(usage=usage.get(disk.node)) for disk in disks)
```

(move `disks = build_disks(capture)` usage accordingly; `used` must be computed from the final tuple). Add a test in `tests/test_linux_usage.py` that `build_inventory` on a hand-built capture carries the usage on `inventory.disks`, and that `snapshot.load` of every committed `linux-*` fixture yields `usage is None` on every disk.
- [ ] **Step 5: Run** the file + `-k "inventory or builder or fixture"`; pyright on `src/lsdsk/adapters/hw/linux`.
- [ ] **Step 6: RED proof for the partition-beats-disk rule**: copy `usage.py` aside, delete the "only when no partition has one" condition, require case 4 to FAIL, restore from the copy (not git).
- [ ] **Step 7: Commit** - `feat(linux): resolve the boot disk and what uses each disk`.

---

### Task 5: Windows reader, capture fields and resolver

**Files:**
- Create: `src/lsdsk/adapters/hw/windows/volumes.py` (impure; add to the coverage `omit` list in `pyproject.toml` beside `windows/reader.py`, same reason)
- Create: `src/lsdsk/adapters/hw/windows/usage.py` (pure)
- Modify: `src/lsdsk/adapters/hw/windows/winapi.py` (constants + prototypes)
- Modify: `src/lsdsk/adapters/hw/windows/capture.py` (`VolumeEntry`, two `WindowsCapture` fields)
- Modify: `src/lsdsk/adapters/hw/windows/reader.py` (`read_system` adds two keys)
- Modify: `src/lsdsk/adapters/hw/windows/builder.py` (`build_inventory` attaches usage)
- Test: `tests/test_windows_usage.py`, `tests/test_windows_volume_parsing.py`

**Interfaces:**
- Produces (capture): `VolumeEntry`: `paths: Entries[DeviceText] = ()`, `disks: Entries[int] = ()`, `esp: bool | None = None`, `error: DeviceText | None = None`. `WindowsCapture.volumes: EntryMap[DeviceText, VolumeEntry] | None = None` (keyed by `\\?\Volume{...}\`), `WindowsCapture.windows_volume: DeviceText | None = None`.
- Produces (pure parsers in `volumes.py`, so they ARE tested on every runner; keep them above the impure part): `parse_disk_extents(raw: bytes) -> list[int]` (disk numbers), `parse_is_esp(raw: bytes) -> bool | None`.
- Produces: `read_volumes(kernel32: api.WinLibrary) -> dict[str, dict[str, Any]]`, `read_windows_volume(kernel32: api.WinLibrary) -> str | None`.
- Produces: `resolve_usage(capture: WindowsCapture, environment: Environment) -> dict[str, DiskUsage | None]`, keyed by node (`PhysicalDrive<n>`).

**Out of scope:** `tests/test_winapi_layout.py` - no new `ctypes.Structure` is declared; the two responses are parsed from bytes at fixed SDK offsets, which is what keeps them testable off Windows.

**STOP conditions:** Task 1 found unelevated volume handles refused wholesale; a disk's `node` is not `PhysicalDrive<n>` on the fixture builder path.

- [ ] **Step 1: Failing parser tests** (`tests/test_windows_volume_parsing.py`, `os_agnostic`): build bytes with `struct.pack`:

```python
ESP = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")


def _extents(*disks: int) -> bytes:
    # VOLUME_DISK_EXTENTS: DWORD count, 4 bytes of padding, then 24-byte DISK_EXTENTs.
    body = b"".join(struct.pack("<I4xqq", disk, 0, 1 << 30) for disk in disks)
    return struct.pack("<I4x", len(disks)) + body


def _partition(style: int, type_guid: uuid.UUID) -> bytes:
    # PARTITION_INFORMATION_EX: style, pad, offset, length, number, two BOOLEANs, pad, then the GPT union.
    head = struct.pack("<I4xqqIBB2x", style, 0, 1 << 20, 1, 0, 0)
    return head + type_guid.bytes_le + bytes(144 - len(head) - 16)
```

Assert: `parse_disk_extents(_extents(1)) == [1]`; spanned `_extents(0, 2) == [0, 2]`; a count larger than the buffer holds returns only the whole extents present; `parse_is_esp(_partition(1, ESP)) is True`; GPT other type -> `False`; MBR style 0 -> `False`; a short buffer -> `None`.

- [ ] **Step 2: Failing resolver tests** (`tests/test_windows_usage.py`), via `WindowsCapture.model_validate` with `disks` entries carrying `node`:
1. `C:` on disk 1 is `windows_volume` -> PhysicalDrive1 `boot=True`, uses `[LETTER ("C:\\",)]`.
2. ESP (`esp=True`, no paths) on disk 0 while `C:` is on disk 1 -> PhysicalDrive0 `boot=True, uses=()`.
3. `D:` on disk 0 and a folder mount `C:\\mnt\\x\\` on disk 0 -> one LETTER use with both paths.
4. Spanned volume `E:` on disks 2 and 3 -> both carry `E:\\`.
5. Volume with `disks=()` and `paths=("X:\\",)` (RAM disk) -> no disk gets `X:`.
6. A volume with `error` set and a disk with nothing -> that disk `None`; a disk WITH a letter stays resolved.
7. `volumes=None` -> every disk `None`.
8. A disk with no volume and no error anywhere -> `DiskUsage(boot=False, uses=())`.

- [ ] **Step 3: Run both** - ImportError.
- [ ] **Step 4: Implement** `winapi.py` additions (constants with the SDK names, then the prototypes in `configure_prototypes`):

```python
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
IOCTL_DISK_GET_PARTITION_INFO_EX = 0x00070048
PARTITION_STYLE_GPT = 1
```

```python
    kernel32.FindFirstVolumeW.restype = wintypes.HANDLE
    kernel32.FindFirstVolumeW.argtypes = (wintypes.LPWSTR, DWORD)
    kernel32.FindNextVolumeW.restype = BOOL
    kernel32.FindNextVolumeW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, DWORD)
    kernel32.FindVolumeClose.restype = BOOL
    kernel32.FindVolumeClose.argtypes = (wintypes.HANDLE,)
    kernel32.GetVolumePathNamesForVolumeNameW.restype = BOOL
    kernel32.GetVolumePathNamesForVolumeNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, DWORD, ctypes.POINTER(DWORD))
    kernel32.GetSystemWindowsDirectoryW.restype = ctypes.c_uint32
    kernel32.GetSystemWindowsDirectoryW.argtypes = (wintypes.LPWSTR, ctypes.c_uint32)
    kernel32.GetVolumePathNameW.restype = BOOL
    kernel32.GetVolumePathNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, DWORD)
    kernel32.GetVolumeNameForVolumeMountPointW.restype = BOOL
    kernel32.GetVolumeNameForVolumeMountPointW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, DWORD)
```

(Check that `wintypes.LPWSTR`/`LPCWSTR`/`HANDLE` are already used in the file - they are pointer-width, so `tests/test_winapi_layout.py`'s width sweep does not apply to argtypes; confirm it only sweeps `_fields_`.)

`volumes.py`: the two pure parsers (offsets as in the test helpers; `_MAX_EXTENTS = 32` with a WHY: a spanned or striped dynamic volume past 32 members is not a configuration anyone runs, and the buffer is fixed), then `read_volumes` (enumerate; per volume: `paths` via `GetVolumePathNamesForVolumeNameW` with a 4096-char buffer; open `volume.rstrip("\\")` with access 0 and `FILE_SHARE_READ | FILE_SHARE_WRITE`; on `INVALID_HANDLE_VALUE` record `error = f"could not open the volume (error {api.last_error()})"` and skip the ioctls; else `disks` from the extents ioctl (an ioctl failure leaves `disks=[]` and records `error`) and `esp` from the partition ioctl (failure leaves it absent); always `CloseHandle`), and `read_windows_volume` (Windows dir -> its mount path -> its volume GUID path; `None` on any failure). In `reader.read_system` add `"volumes": volumes.read_volumes(tree.kernel32)` and `"windows_volume": volumes.read_windows_volume(tree.kernel32)`.

`usage.py`: map `PhysicalDrive<n>` -> uses; `boot` for the disks of `windows_volume` and of any `esp=True` volume; the undecidable rule from Global Constraints; `None` everywhere for `CONTAINER` or `volumes is None`. Wire into `builder.build_inventory` exactly as Task 4 Step 4 does.

- [ ] **Step 5: Run** both files, `-k "windows"`, pyright on `src/lsdsk/adapters/hw/windows` - 0 errors; and `--pythonplatform Windows` pyright on the same path.
- [ ] **Step 6: Real Windows run** - per the memory fact on vm-pydev-win: tar the worktree source in, build `.venv-win` there, run `lsdsk disks --format json` elevated AND via the `/RL LIMITED` scheduled task from Task 1; on vk-rnowotny run it elevated. Required: `C:` lands on the disk the probe found, `boot` on it, the ESP disk boot, the BitLocker-locked `E:` mapped.
- [ ] **Step 7: Commit** - `feat(windows): read volumes and resolve the boot disk and drive letters`.

---

### Task 6: Render - the column, the panel, the legend

**Files:**
- Modify: `src/lsdsk/adapters/render/theme.py` (`NOT_MOUNTED`, `format_usage`)
- Modify: `src/lsdsk/adapters/render/tables.py` (`DISK_COLUMNS`, `disk_table_row`)
- Modify: `src/lsdsk/adapters/render/detail.py` (`disk_detail` identity group)
- Test: `tests/test_used_by_column.py`

**Interfaces:**
- Consumes: Task 2 models.
- Produces: `theme.NOT_MOUNTED: Final = "not mounted"`, `theme.format_usage(usage: DiskUsage | None) -> Cell`; disk row key `"used_by"`, column title `"used by"`.

**Out of scope:**
- `adapters/tui/app.py` - its `DISK_COLUMNS` derives from `tables.DISK_COLUMNS` and its rows from `disk_table_row`, so it gains the column with no edit; if a TUI test fails, that is a real finding, not a reason to edit the TUI.
- `adapters/render/tree.py` / `report.render_controller_disks` - the trees draw disks under controllers and are not asked to carry usage.
- `adapters/config/*` - no new key (Global Constraints).

**STOP conditions:** `test_link_bandwidth.py`'s guard that `size` survives at `DEFAULT_PIPED_WIDTH` fails after the new column is added - report the measured widths instead of retuning priorities blind.

- [ ] **Step 1: Failing tests** (`tests/test_used_by_column.py`, `os_agnostic`):

```python
@pytest.mark.parametrize(
    ("usage", "text"),
    [
        (None, "-"),
        (DiskUsage(), "not mounted"),
        (DiskUsage(boot=True, uses=(DiskUse(kind=UseKind.ZFS, name="rpool"),)), "boot zfs:rpool"),
        (DiskUsage(boot=True, uses=(DiskUse(kind=UseKind.LETTER, mounts=("C:\\",)),)), "boot C:\\"),
        (DiskUsage(uses=(DiskUse(kind=UseKind.LVM, name="vg0", mounts=("/var", "/srv")),)), "lvm:vg0 -> /var, /srv"),
        (DiskUsage(uses=(DiskUse(kind=UseKind.MOUNT, mounts=("/",)), DiskUse(kind=UseKind.SWAP))), "/, swap"),
        (DiskUsage(boot=True), "boot"),
    ],
)
def test_a_usage_is_written_in_one_short_cell(usage: DiskUsage | None, text: str) -> None:
    assert theme.format_usage(usage)[0] == text
```

Decide here whether a letter is drawn `C:` or `C:\` (the volume path is `C:\`): draw `C:` for a root path of a drive letter (`len == 3 and endswith(":\\")`), the whole path for a folder mount, and change the parametrised row to `"boot C:"`. Plus:
- `test_an_unread_usage_is_styled_as_unknown` - `format_usage(None)[1] == theme.STYLE_UNKNOWN`; "not mounted" is NOT styled unknown.
- `test_the_disks_table_heads_a_used_by_column_with_each_disk_s_cell` - render `render_disks` of a hand-built `Inventory` with two disks (usages from above) at width 200 through a recording Console; assert the header line contains `used by` and the row of `/dev/sde` contains `boot zfs:rpool` (compare whitespace-stripped).
- `test_the_used_by_column_is_the_first_dropped_on_a_narrow_page` - at `DEFAULT_PIPED_WIDTH` (120) with a committed fixture: `used by` absent, `size` present.
- `test_the_detail_panel_carries_the_whole_usage_and_names_the_unread_marker` - `disk_detail` for a disk with `usage=None` renders `used by` with `-` and the legend names the unread marker; for a resolved disk the full text appears.
- `test_a_committed_fixture_reads_not_read_never_not_mounted` - render `disks` from every committed fixture at width 250: no row contains `not mounted`.

- [ ] **Step 2: Run** - FAIL.
- [ ] **Step 3: Implement.** `theme.py`:

```python
#: A usage reading that was taken and found nothing using the disk. Distinct
#: from NOT_READ, which says nobody could look.
NOT_MOUNTED: Final = "not mounted"


def _use_text(use: DiskUse) -> str:
    if use.kind is UseKind.SWAP:
        return "swap"
    if use.kind in {UseKind.MOUNT, UseKind.LETTER}:
        return ", ".join(_mount_text(mount) for mount in use.mounts)
    label = f"{use.kind.value}:{use.name}"
    # A pool's datasets are never listed (ADR 0004), whatever the reader found.
    if use.kind is UseKind.ZFS or not use.mounts:
        return label
    return f"{label} -> {', '.join(use.mounts)}"


def _mount_text(mount: str) -> str:
    # A drive letter's volume path is "C:\"; the letter alone is what a reader knows it by.
    return mount[:2] if len(mount) == 3 and mount.endswith(":\\") else mount


def format_usage(usage: DiskUsage | None) -> Cell:
    """... Google docstring with Args/Returns and two doctests (None -> '-', boot zfs) ..."""
    if usage is None:
        return NOT_READ, STYLE_UNKNOWN
    parts = [_use_text(use) for use in usage.uses]
    if usage.boot:
        parts.insert(0, "boot")
    if not parts:
        return NOT_MOUNTED, ""
    head, *rest = parts
    return (f"{head} {', '.join(rest)}" if usage.boot and rest else ", ".join(parts)), ""
```

(Make the boot join produce `boot zfs:rpool` and `boot /, swap`; adjust if the expression reads awkwardly - the tests decide.) Add both new names to `__all__`. `tables.py`: append `Column("used_by", "used by", priority=8, flexible=True, min_width=8)` to `DISK_COLUMNS` and `"used_by": theme.format_usage(disk.usage)` to `disk_table_row` (update its docstring's column count: "twelve" -> "thirteen"). `detail.py`: add `("used by", row["used_by"])` as the FIRST pair of the IDENTITY group (it answers the question before the panel is read further).
- [ ] **Step 4: Run** the new file, then the whole suite (`env -u VIRTUAL_ENV .venv/bin/python -m pytest tests -q -p no:cacheprovider -m "not local_only"`). Expected remaining failure: only the SKILL.md field-enumeration test from Task 2. Any width/TUI/screenshot guard failing is a real finding - read it before changing anything.
- [ ] **Step 5: Commit** - `feat(render): a used-by column, in the disk table and the detail panel`.

---

### Task 7: Real-hardware check and ADR amendment

**Files:**
- Modify: `tests/e2e/hostprobe.py` (one new check in the disks probe)
- Modify: `docs/adr/0004-boot-drive-and-what-uses-a-disk.md` (drop `display.used_by_width`; append Task 1's result)

- [ ] **Step 1: Add the check** in the function that already parses `disks --format json` (near `hostprobe.py:159-200`):

```python
    usages = [d.get("usage") for d in disks if str(d.get("bus")) != "virtual"]
    booted = [u for u in usages if u and u.get("boot")]
    results.append(
        check(
            "usage:boot-disk-found",
            bool(booted) or all(u is None for u in usages),
            f"{len(booted)} boot disk(s) among {len(usages)}; all unread means a container",
        )
    )
```

- [ ] **Step 2: Run** `make testintegration` (about a minute per host; see the project memory fact on its timing). Required: every reachable host passes the new check; on the two ZFS-root hosts exactly the two mirror members carry `boot`; read the recorded facts, not just the pass line.
- [ ] **Step 3: Amend ADR 0004**: in "Where", replace "and capped at `display.used_by_width` with a marked cut" with "flexible and clipped with a marked cut like `model`, with no configuration key: a key no committed capture can move would fail the live-key harness, and nothing in this cell grows without bound the way an NVMe WWN does"; append Task 1's measured paragraph to Consequences.
- [ ] **Step 4: Commit** - `test(e2e): the boot disk is found on every real host; ADR 0004 amended`.

---

### Task 8: Docs, skill, changelog

**Files:**
- Modify: `skills/lsdsk/SKILL.md` - ONLY via `bitranox:meta-skill-writer`: add `usage` to the sentence enumerating a disk's fields, and one line on the `used by` column's three states.
- Modify: `README.md`, `COMMANDS.md` (and their `de/` twins; refresh the manifest per doc), `CHANGELOG.md` (Unreleased: added), `CLAUDE.md` (untracked; add `linux/mounts.py`, `linux/usage.py`, `windows/volumes.py`, `windows/usage.py` to the structure tree), `docs/systemdesign/module_reference.md` (the four modules).

- [ ] **Step 1:** Grep the renderer for every label you quote (`used by`, `not mounted`, `boot`) before writing it into a doc.
- [ ] **Step 2:** Invoke `bitranox:meta-skill-writer` for the SKILL.md edit; then run every test that reads an edited file: `grep -rln "SKILL.md\|README.md\|COMMANDS.md\|CHANGELOG.md" tests` and run those files.
- [ ] **Step 3:** `scripts/translation_manifest.py --refresh <doc>` for each twinned doc edited, after translating the change.
- [ ] **Step 4:** Full gate: `env -u VIRTUAL_ENV make test` via `python3 <compuse-toolbox>/scripts/gate.py --gate "env -u VIRTUAL_ENV make test"`; must end `{"result":"pass"}`.
- [ ] **Step 5: Commit** - `docs: the boot drive and the used-by column`.

---

## Self-review notes

- Spec coverage: sources (Tasks 3, 5), boot rule (4, 5), partition-beats-disk (4 case 4 + RED proof), ZFS pool not datasets (4 case 3, 6), three states (6), container (4 case 10, 5), placement column + panel + JSON (2, 6), e2e (7), docs/ADR (7, 8). The ADR's `used_by_width` is deliberately dropped and the ADR amended (Task 7).
- Names used across tasks: `DiskUse`, `DiskUsage`, `UseKind`, `Disk.usage`, `resolve_usage(capture, environment)`, `theme.format_usage`, `theme.NOT_MOUNTED`, row key `used_by`.
