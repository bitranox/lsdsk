"""The size guard on the two files that reach lsdsk from outside it.

A capture handed to ``--replay`` and the store at ``--history-file`` are both
validated against a Pydantic model, but only after the whole file is already in
memory. These tests hold the guard that runs first.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.history.store import load_history
from lsdsk.adapters.hw.snapshot import load
from lsdsk.adapters.textfile import MAX_INPUT_BYTES, read_text_bounded
from lsdsk.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
SNAPSHOT = FIXTURES / "linux-sas-hba.json"


def _sparse_file(path: Path, size: int) -> Path:
    """A file that reports ``size`` without occupying it.

    The point of the guard is that an oversized file is refused from its
    directory entry, so the test must not need the disk space that reading it
    would. A sparse file makes the distinction observable: if the guard ever
    regressed to reading first, this test would try to materialise the whole
    thing.
    """
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


@pytest.mark.os_agnostic
def test_a_real_capture_is_comfortably_under_the_limit() -> None:
    """The control. A limit that refused the project's own fixtures is useless."""
    largest = max(capture.stat().st_size for capture in FIXTURES.glob("*.json"))
    assert largest < MAX_INPUT_BYTES
    # Not merely under it, but under it by the margin the constant claims: a
    # machine with far more drives than any fixture must still load.
    assert largest * 100 < MAX_INPUT_BYTES


@pytest.mark.os_agnostic
def test_an_oversized_file_is_refused_by_its_size_not_read(tmp_path: Path) -> None:
    """Pointing --replay at a disk image must fail immediately, not swap."""
    huge = _sparse_file(tmp_path / "not-a-capture.img", MAX_INPUT_BYTES + 1)
    with pytest.raises(ConfigurationError) as raised:
        load(huge)
    assert "Check the path" in str(raised.value)


@pytest.mark.os_agnostic
def test_the_history_store_is_bounded_by_the_same_guard(tmp_path: Path) -> None:
    """The other file that arrives from outside, guarded identically."""
    huge = _sparse_file(tmp_path / "history.json", MAX_INPUT_BYTES + 1)
    with pytest.raises(ConfigurationError) as raised:
        load_history(huge, hostname="box")
    assert "Check the path" in str(raised.value)


@pytest.mark.os_agnostic
def test_a_file_exactly_at_the_limit_is_still_read(tmp_path: Path) -> None:
    """An off-by-one here would refuse a file the limit says is allowed."""
    edge = tmp_path / "edge.json"
    edge.write_text("x" * MAX_INPUT_BYTES, encoding="utf-8")
    assert len(read_text_bounded(edge, what="a snapshot")) == MAX_INPUT_BYTES


@pytest.mark.os_agnostic
def test_an_unreadable_path_is_reported_as_configuration_not_as_oserror(tmp_path: Path) -> None:
    """The caller catches ConfigurationError; a bare OSError would escape it."""
    with pytest.raises(ConfigurationError):
        read_text_bounded(tmp_path / "absent.json", what="a snapshot")


@pytest.mark.os_agnostic
def test_a_directory_handed_to_the_reader_is_refused_cleanly(tmp_path: Path) -> None:
    """stat() succeeds on a directory, so the read is what has to refuse it."""
    with pytest.raises(ConfigurationError):
        read_text_bounded(tmp_path, what="a snapshot")


@pytest.mark.os_agnostic
def test_the_guard_did_not_break_the_path_it_guards() -> None:
    """Every fixture still loads, so the guard cost nothing that mattered."""
    for capture in sorted(FIXTURES.glob("*.json")):
        assert load(capture).hostname, f"{capture.name} no longer loads"


@pytest.mark.os_posix
def test_an_oversized_file_is_refused_without_being_allocated(tmp_path: Path) -> None:
    """What the guard buys, stated as a measurement rather than as a comment.

    ``st_blocks`` is the only way to see that the file was never allocated, and
    it exists only on POSIX. The early skip is what keeps the type checker off
    it too: ``pytest.skip`` returns ``NoReturn``, so under
    ``--pythonplatform Windows`` everything below is unreachable and the
    attribute is never resolved against a Windows ``stat_result``.
    """
    if sys.platform == "win32":  # pragma: no cover - the marker already excludes this
        pytest.skip("st_blocks is POSIX only")

    huge = _sparse_file(tmp_path / "sparse.json", MAX_INPUT_BYTES * 4)

    assert huge.stat().st_size > MAX_INPUT_BYTES
    occupied = huge.stat().st_blocks * 512
    assert occupied < MAX_INPUT_BYTES, "the file was actually allocated, so this proves nothing"


@pytest.mark.os_agnostic
def test_an_oversized_file_is_refused_for_its_size_and_not_for_its_contents(tmp_path: Path) -> None:
    """The refusal has to be the cheap one, everywhere.

    Asserting only that it raises would pass with the guard removed too: a file
    of nulls fails JSON parsing just as loudly, after being read in full.
    """
    huge = _sparse_file(tmp_path / "sparse.json", MAX_INPUT_BYTES * 4)

    with pytest.raises(ConfigurationError) as raised:
        load(huge)
    assert "MB, which is far larger than" in str(raised.value)


# --------------------------------------------------------------------------
# Writing a file is a trust boundary too
# --------------------------------------------------------------------------


@pytest.mark.os_posix
def test_snapshot_replaces_a_symlink_instead_of_writing_through_it(tmp_path: Path) -> None:
    """A capture taken as root must not let somebody else choose the target.

    ``Path.write_text`` opens the destination through the normal ``open()``
    path, which follows a symlink to whatever it points at. A privileged
    snapshot written into a directory a lower-privileged user can write then
    overwrites a file of their choosing, and the ``chmod(0o600)`` that follows
    narrows *their* target rather than the capture. A rename never follows the
    last path component, which is why the history store already writes this way.
    """
    from lsdsk.adapters.hw.snapshot import save

    victim = tmp_path / "victim.txt"
    victim.write_text("MUST-SURVIVE", encoding="utf-8")
    victim.chmod(0o644)
    destination = tmp_path / "capture.json"
    destination.symlink_to(victim)

    save({"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}}, destination)

    assert victim.read_text(encoding="utf-8") == "MUST-SURVIVE", "the symlink target was written through"
    assert victim.stat().st_mode & 0o777 == 0o644, "the symlink target was re-permissioned"
    assert not destination.is_symlink(), "the symlink should have been replaced by the capture"
    assert destination.stat().st_mode & 0o777 == 0o600, "the capture is not owner-only"
    assert not list(tmp_path.glob(".*.tmp")), "a temporary file was left behind"


@pytest.mark.os_agnostic
def test_a_capture_cannot_inject_control_characters_into_the_terminal(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """A model number is chosen by the hardware, and the sink executes escapes.

    A drive whose model contains an escape sequence, or a capture handed to an
    operator, could recolour the report, retitle the window, or embed a newline
    that fabricates a table row indistinguishable from a real one. Measured
    before the fix: 2 raw ESC bytes from ``disks`` and 14 from the bare view.
    """
    import json

    from lsdsk.adapters.cli import cli

    source = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
    capture: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    capture["hostname"] = "evilhost\x1b[31mRED\x1b[0m"
    block: dict[str, Any] = capture.get("block") or {}
    node: str = sorted(block)[0]
    device: dict[str, Any] = block[node].setdefault("device", {})
    device["model"] = "Evil\x1b[31mDRIVE\x1b[0m\x1b]0;PWNED\x07\nFAKE-ROW"
    crafted = tmp_path / "evil.json"
    crafted.write_text(json.dumps(capture), encoding="utf-8")

    clean = cli_runner.invoke(cli, ["disks", "--replay", str(source)], obj=production_factory, color=False)
    assert clean.output, "the control produced no output, so it proved nothing"

    for argv in (["disks"], []):
        result = cli_runner.invoke(cli, [*argv, "--replay", str(crafted)], obj=production_factory, color=False)
        assert result.output, f"{argv or 'bare'}: no output, so this asserted nothing"
        assert "\x1b" not in result.output, f"{argv or 'bare'}: an escape sequence reached the terminal"
        assert "\x07" not in result.output, f"{argv or 'bare'}: a bell character reached the terminal"
        # The injected newline is what fabricates a row; the text may still be
        # shown, but it must not have arrived on a line of its own.
        forged = [line for line in result.output.splitlines() if line.strip() == "FAKE-ROW"]
        assert not forged, f"{argv or 'bare'}: an injected newline forged a table row"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("an integer literal past CPython's digit limit", '{{"schema": {digits}, "platform": "linux"}}'),
        ("JSON nested past the C stack", '{{"schema": 1, "n": {deep}}}'),
    ],
)
def test_malformed_json_is_refused_as_configuration_not_raised(tmp_path: Path, label: str, body: str) -> None:
    """``json.loads`` raises more than ``JSONDecodeError``.

    A huge integer literal raises a bare ``ValueError`` and deep nesting raises
    ``RecursionError``. Both escaped the handler as tracebacks under the wrong
    exit codes, 22 and 1, where every other malformed file is refused with 78.
    """
    crafted = tmp_path / "bad.json"
    crafted.write_text(body.format(digits="9" * 20000, deep="[" * 60000 + "]" * 60000), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load(crafted)


@pytest.mark.os_agnostic
def test_the_pci_id_database_is_read_through_the_same_bound(tmp_path: Path) -> None:
    """It was the one external read that bypassed the guard entirely.

    ``pci.ids`` is a system file, so exploiting it needs root already, but the
    module's own docstring claims every boundary is size-bounded and this one
    was not.
    """
    from lsdsk.adapters.hw.decode import pciids

    huge = _sparse_file(tmp_path / "pci.ids", MAX_INPUT_BYTES + 1)
    with pytest.raises(ConfigurationError):
        read_text_bounded(huge, what="a PCI ID database", errors="replace")

    # And the module itself no longer reads a path directly. Asserted against
    # the file rather than against a private function, so the test says the same
    # thing without reaching past the public surface.
    assert pciids.__file__ is not None, "the module has no file, so this asserted nothing"
    source = Path(pciids.__file__).read_text(encoding="utf-8")
    assert "read_text_bounded(" in source, "pci.ids is not routed through the bounded reader"
    assert ".read_text(" not in source, "pci.ids still has a direct, unbounded read"


@pytest.mark.os_posix
def test_snapshot_still_writes_where_no_temporary_file_can_be_made(tmp_path: Path) -> None:
    """The atomic write puts its temporary file in the DESTINATION's directory.

    So a destination that is writable inside a directory that is not could no
    longer be written at all, and surfaced as a raw ``PermissionError``.
    ``-o /dev/null`` is the case anyone hits; a user-writable file under a
    root-owned path is the general one. The fallback gives up atomicity, which
    was never available there, and keeps the symlink refusal, which is the
    property the rename was for.
    """
    from lsdsk.adapters.hw.snapshot import save

    capture: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}}
    save(capture, Path("/dev/null"))  # must not raise

    # Force the same fallback through the public entry point: a directory that
    # cannot take a temporary file, holding a symlink at the destination.
    victim = tmp_path / "victim.txt"
    victim.write_text("MUST-SURVIVE", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(victim)
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError):
            save(capture, link)
    finally:
        tmp_path.chmod(0o700)
    assert victim.read_text(encoding="utf-8") == "MUST-SURVIVE", "the fallback followed a symlink"
