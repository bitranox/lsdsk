r"""No text mountinfo carries can refuse the run.

mountinfo is decoded so that a byte which is not UTF-8 survives as a lone
surrogate, because a MOUNTPOINT must be asked at its own bytes. That decode
covered the whole line, so the same byte in any OTHER field - a FUSE subtype is
the user's own choice - reached the capture as a lone surrogate, which no UTF
encoding can write: the capture model refused the reading and every command,
``snapshot`` included, ended with exit 78. A mountpoint of many such bytes was
refused too, because its printable escape is four characters a byte and ran past
the text bound the capture applies.

Every field but the mountpoint is now recorded with each such byte replaced, the
capture model replaces one a replayed file still carries, and a mountpoint whose
printable form would pass the bound is cut, with a mark, between whole escapes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.cli.main import main
from lsdsk.adapters.hw import snapshot
from lsdsk.adapters.hw.linux import mounts
from lsdsk.adapters.hw.linux.capture import MountEntry
from lsdsk.composition import build_production
from lsdsk.domain.text import MAX_DEVICE_TEXT

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-zfs-filesystems.json"
ARGV_AFTER_REPLAY = ["--no-record", "filesystems"]
# The exit code a refused reading ends with, which no row of mountinfo may cause.
REFUSED = 78

# A node no machine has; the reader is handed a resolver that answers for it, so
# the row is kept on every platform.
SOURCE = "/dev/lsdsk-test-absent1"
_SURROGATES = range(0xD800, 0xE000)


def _resolver(source: str) -> str | None:
    return "8:17" if source == SOURCE else None


def _rows(tmp_path: Path, *lines: bytes) -> list[dict[str, str | bool]]:
    """Rows the real reader returns for a mountinfo holding `lines` below a root mount."""
    path = tmp_path / "mountinfo"
    # Bytes, so no platform rewrites a line ending or re-encodes a byte.
    path.write_bytes(b"1 0 8:1 / / rw - ext4 /dev/lsdsk-test-absent0 rw\n" + b"".join(lines))
    rows = mounts.read_mounts(path, device_number=_resolver)
    assert rows is not None
    return rows


def _reading_with(rows: list[dict[str, str | bool]]) -> dict[str, Any]:
    reading: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    reading["mounts"] = [*reading["mounts"], *rows]
    return reading


def _replayed(tmp_path: Path, reading: dict[str, Any], name: str) -> int:
    path = tmp_path / f"{name}.json"
    # json.dumps writes a lone surrogate as its \udcXX escape, which is how a
    # snapshot carrying one is spelled on disk.
    path.write_text(json.dumps(reading), encoding="utf-8")
    return main(["--replay", str(path), *ARGV_AFTER_REPLAY], services_factory=build_production)


def _has_lone_surrogate(text: str) -> bool:
    return any(ord(character) in _SURROGATES for character in text)


# A FUSE block mount (ntfs-3g and kin) on a real device, its subtype chosen by
# whoever mounted it: one byte that is not UTF-8 in the type.
_FUSEBLK_ROW = b"2 1 8:17 / /mnt/x rw - fuseblk.\xff " + SOURCE.encode() + b" rw\n"


def test_an_undecodable_byte_outside_the_mountpoint_does_not_refuse_a_live_reading(tmp_path: Path) -> None:
    rows = _rows(tmp_path, _FUSEBLK_ROW)
    assert any(row["dev"] == "8:17" for row in rows), rows

    inventory = snapshot.build_from(_reading_with(rows))

    assert inventory.hostname == "linux-zfs-filesystems"
    assert snapshot.serialise(_reading_with(rows))


def test_no_field_but_the_mountpoint_carries_a_lone_surrogate_into_the_capture(tmp_path: Path) -> None:
    rows = _rows(
        tmp_path,
        _FUSEBLK_ROW,
        # A ZFS dataset whose pool name holds the byte: the pool name is recorded.
        b"3 1 0:50 / /p rw - zfs po\xffol/data rw\n",
        # A device number field the reader did not expect: recorded as text all the same.
        b"4 1 8\xff:2 / /q rw - ext4 /dev/lsdsk-test-absent2 rw\n",
        # The mountpoint keeps its byte, so it can still be asked at it.
        b"5 1 8:3 / /caf\xe9 rw - ext4 /dev/lsdsk-test-absent3 rw\n",
    )
    recorded = {str(row["dev"]): row for row in rows}

    assert recorded["8:17"]["fstype"] == "fuseblk.\ufffd"
    assert recorded["0:50"]["source"] == "po\ufffdol"
    assert "8\ufffd:2" in recorded, sorted(recorded)
    for row in rows:
        others = [value for key, value in row.items() if key != "mountpoint" and isinstance(value, str)]
        assert not any(_has_lone_surrogate(value) for value in others), row
    assert mounts.kernel_path(str(recorded["8:3"]["mountpoint"])) == b"/caf\xe9"


@pytest.mark.parametrize("field", ["fstype", "source", "source_dev", "dev"])
def test_a_replayed_capture_with_an_undecodable_field_runs_like_its_ascii_control(
    field: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A capture written before the reader replaced the byte still carries it as a lone surrogate."""
    row: dict[str, str | bool] = {
        "dev": "8:17",
        "mountpoint": "/mnt/x",
        "fstype": "fuseblk",
        "source": "",
        "source_dev": "8:17",
        "read_only": False,
    }
    control = _replayed(tmp_path, _reading_with([row]), "control")
    capsys.readouterr()

    arm = _replayed(tmp_path, _reading_with([{**row, field: f"{row[field]}\udcff"}]), "arm")
    said = capsys.readouterr().err

    assert control != REFUSED
    assert arm == control, said


def test_a_replayed_text_field_has_each_lone_surrogate_replaced() -> None:
    entry = MountEntry.model_validate({"dev": "8:1\udc80", "mountpoint": "/", "fstype": "a\ud800-\udbff-\udc00-\udfff"})

    assert entry.dev == "8:1\ufffd"
    assert entry.fstype == "a\ufffd-\ufffd-\ufffd-\ufffd"


def test_a_mountpoint_of_many_undecodable_bytes_does_not_refuse_the_reading(tmp_path: Path) -> None:
    # Each such byte is four characters once escaped, so 1100 of them were
    # 4400 characters against a bound of 4096.
    rows = _rows(tmp_path, b"2 1 8:2 / /" + b"\xff" * 1100 + b" rw - ext4 /dev/lsdsk-test-absent2 rw\n")

    snapshot.build_from(_reading_with(rows))
    (stored,) = [row for row in json.loads(json.dumps(rows)) if row["dev"] == "8:2"]
    entry = MountEntry.model_validate(stored)

    assert len(entry.mountpoint) <= MAX_DEVICE_TEXT
    assert entry.mountpoint.endswith("...")
    # Cut between whole escapes: no dangling "\x" or "\xf" reads as another byte.
    shown = entry.mountpoint.removeprefix("/").removesuffix("...")
    assert shown == "\\xff" * (len(shown) // 4), shown[-8:]


def test_a_mountpoint_longer_than_the_text_bound_is_cut_with_a_mark(tmp_path: Path) -> None:
    long_name = b"/" + b"d" * (MAX_DEVICE_TEXT + 500)
    rows = _rows(tmp_path, b"2 1 8:2 / " + long_name + b" rw - ext4 /dev/lsdsk-test-absent2 rw\n")
    (row,) = [row for row in rows if row["dev"] == "8:2"]

    snapshot.build_from(_reading_with(rows))
    entry = MountEntry.model_validate(row)

    # The reader still holds every byte, to ask the system at.
    assert mounts.kernel_path(str(row["mountpoint"])) == long_name
    assert len(entry.mountpoint) == MAX_DEVICE_TEXT
    assert entry.mountpoint == long_name.decode()[: MAX_DEVICE_TEXT - 3] + "..."


def test_a_mountpoint_exactly_at_the_bound_is_kept_whole() -> None:
    exact = "/" + "d" * (MAX_DEVICE_TEXT - 1)

    assert MountEntry.model_validate({"dev": "8:2", "mountpoint": exact}).mountpoint == exact
