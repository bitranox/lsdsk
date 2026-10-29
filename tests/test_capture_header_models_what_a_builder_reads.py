"""The capture header types only the keys a builder reads; the rest of what a reader writes is kept, untyped.

The header once required ``kernel``, which no builder reads: a capture whose
kernel line was missing was refused as "not a snapshot" although every reading
the tool reports from was in it. The readers still WRITE the kernel - a person
reading a fixture needs it, since what a Linux capture can hold depends on the
kernel version - and a capture carrying it still loads, because a key the model
does not name is ignored. ``hostname`` stays required: it is the guard that
keeps an arbitrary JSON object from replaying as an empty machine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw import snapshot
from lsdsk.domain.errors import ConfigurationError

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _committed_without(tmp_path: Path, name: str, key: str) -> tuple[Path, dict[str, Any]]:
    capture: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    del capture[key]
    target = tmp_path / f"{name}.json"
    target.write_bytes(json.dumps(capture).encode("utf-8"))
    return target, capture


@pytest.mark.parametrize("name", ["linux-minimal", "windows-ahci"])
def test_a_capture_without_a_kernel_line_still_replays(tmp_path: Path, name: str) -> None:
    path, capture = _committed_without(tmp_path, name, "kernel")

    assert snapshot.load(path).hostname == capture["hostname"]


@pytest.mark.parametrize("name", ["linux-minimal", "windows-ahci"])
def test_a_capture_without_a_hostname_is_still_refused(tmp_path: Path, name: str) -> None:
    """The control: dropping one required key did not drop the guard the others are."""
    path, _ = _committed_without(tmp_path, name, "hostname")

    with pytest.raises(ConfigurationError, match="hostname: Field required"):
        snapshot.load(path)


def test_every_committed_capture_still_records_its_kernel() -> None:
    """Provenance a person reads: the model no longer types it, and no capture lost it."""
    captures = sorted(FIXTURES.glob("*.json"))

    assert captures, "no committed captures were found, so this checked nothing"
    missing = [path.name for path in captures if "kernel" not in json.loads(path.read_text(encoding="utf-8"))]
    assert not missing, f"these captures no longer carry a kernel line: {missing}"
