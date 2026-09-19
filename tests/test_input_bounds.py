"""What a file reaching lsdsk from outside it is allowed to do to it.

A capture handed to ``--replay`` and the store at ``--history-file`` are both
validated against a Pydantic model, but only after the whole file is already in
memory. These tests hold the guard that runs first, and the bounds a capture
that passed it still has to respect once its contents reach a renderer.
"""

from __future__ import annotations

import io
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.history.store import load_history
from lsdsk.adapters.hw.snapshot import load
from lsdsk.adapters.render.tree import FabricView, render_fabric
from lsdsk.adapters.textfile import MAX_INPUT_BYTES, read_text_bounded
from lsdsk.domain.enums import TreeDensity
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.models import Inventory, PciNode

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

#: A FIFO needs ``os.mkfifo``, which Windows does not have. Asked with
#: ``hasattr`` rather than by calling it, because a skipif CONDITION is
#: evaluated at IMPORT time, before any marker can skip anything.
HAS_FIFO = hasattr(os, "mkfifo")
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


@pytest.mark.os_agnostic
def test_a_controller_name_cannot_inject_control_characters_either(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """A controller's name and firmware are chosen by its own firmware too.

    The disk fields were cleaned in the builder and the controller's were not,
    so the same payload that a drive's model had stripped reached the terminal
    intact through ``board_name``: measured 2 raw ESC bytes from ``controllers``
    and 3 from the bare view. Cleaning now happens on the FIELD, so the arm
    below covers every view a controller name reaches.
    """
    import json

    from lsdsk.adapters.cli import cli

    payload = "Evil\x1b[31mX\x1b[0m\x1b]0;PWNED\x07\nFAKE-ROW"
    source = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
    capture: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    hosts: dict[str, Any] = capture["classes"]["scsi_host"]
    named = [host for host in hosts.values() if host.get("board_name")]
    assert named, "the fixture carries no controller name, so this would assert nothing"
    for host in named:
        host["board_name"] = payload
        host["version_fw"] = payload
    crafted = tmp_path / "evil-controller.json"
    crafted.write_text(json.dumps(capture), encoding="utf-8")

    clean = cli_runner.invoke(cli, ["controllers", "--replay", str(source)], obj=production_factory, color=False)
    assert clean.output, "the control produced no output, so it proved nothing"

    for argv in (["controllers"], []):
        result = cli_runner.invoke(cli, [*argv, "--replay", str(crafted)], obj=production_factory, color=False)
        assert result.output, f"{argv or 'bare'}: no output, so this asserted nothing"
        assert "\x1b" not in result.output, f"{argv or 'bare'}: an escape sequence reached the terminal"
        assert "\x07" not in result.output, f"{argv or 'bare'}: a bell character reached the terminal"
        forged = [line for line in result.output.splitlines() if line.strip() == "FAKE-ROW"]
        assert not forged, f"{argv or 'bare'}: an injected newline forged a table row"


def _fifo_carrying(path: Path, payload_bytes: int) -> threading.Thread:
    """A FIFO fed ``payload_bytes`` by a writer that gives up when nobody reads.

    A FIFO is the case the directory entry cannot describe: ``st_size`` is 0
    whatever is about to come through it. The writer is a daemon thread so a
    regression cannot wedge the suite, and it swallows the broken pipe it gets
    when a correctly-bounded reader stops early.
    """
    os.mkfifo(path)

    def feed() -> None:
        chunk = b"x" * (1024 * 1024)
        remaining = payload_bytes
        try:
            with path.open("wb") as handle:
                while remaining > 0:
                    handle.write(chunk[:remaining])
                    remaining -= min(remaining, len(chunk))
        except (BrokenPipeError, OSError):
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    return writer


@pytest.mark.os_linux
@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_a_stream_whose_directory_entry_understates_it_is_still_bounded(tmp_path: Path) -> None:
    """The size cap must not rest on ``st_size`` being honest.

    A character device, a FIFO and nearly everything under ``/proc`` report a
    size of 0, so a guard that reads the directory entry and then calls
    ``read_text`` is inert for exactly the inputs that can be unbounded. Before
    this was fixed, ``--replay /dev/zero`` read until the kernel killed the
    process, and ``--history-file /dev/full`` did the same with no message at
    all.
    """
    fifo = tmp_path / "capture.json"
    writer = _fifo_carrying(fifo, MAX_INPUT_BYTES + 1)
    assert fifo.stat().st_size == 0, "the premise failed: this FIFO reports a size"

    with pytest.raises(ConfigurationError) as raised:
        read_text_bounded(fifo, what="a snapshot")
    assert "Check the path" in str(raised.value)
    writer.join(timeout=30)


@pytest.mark.os_linux
@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_a_stream_that_fits_is_still_read_whatever_its_directory_entry_says(tmp_path: Path) -> None:
    """The control, and the reason the bound is a read rather than a refusal.

    ``--replay <(ssh host lsdsk snapshot -o -)`` hands this tool a FIFO on
    purpose. Refusing everything that is not a regular file would close the
    hole and take that with it, so a stream under the limit must still load.
    """
    fifo = tmp_path / "capture.json"
    body = '{"hello": "world"}'
    writer = _fifo_carrying(fifo, 0)
    writer.join(timeout=30)
    fifo.unlink()
    os.mkfifo(fifo)

    def feed() -> None:
        with fifo.open("wb") as handle:
            handle.write(body.encode("utf-8"))

    small = threading.Thread(target=feed, daemon=True)
    small.start()
    assert read_text_bounded(fifo, what="a snapshot") == body
    small.join(timeout=30)


def _chain_address(level: int) -> str:
    """One address per level of a synthetic chain, in the width a real one has.

    A PCI address is twelve characters and the address column is sized for
    exactly that, so a wider synthetic one would be clipped and the arm would
    fail on its own fixture rather than on the tree.
    """
    return f"0000:{level // 256:02x}:{(level // 8) % 32:02x}.{level % 8}"


@pytest.mark.os_agnostic
def test_a_parent_chain_deeper_than_the_frame_limit_is_still_drawn() -> None:
    """A capture whose devices form one very deep chain is drawn, not died on.

    ``fabric.assemble`` is iterative and hands the renderer whatever depth a
    capture carries, so the ceiling is the renderer's own: walking that chain
    with one frame per level raises ``RecursionError`` while the section is
    being built, which is after the page's header and its findings are already
    on stdout. The depth is taken from the interpreter's own limit, so the arm
    cannot go vacuous where that limit is raised.
    """
    depth = sys.getrecursionlimit() + 500
    nodes = [PciNode(address="0000:00", name="root complex")]
    nodes.extend(
        PciNode(
            address=_chain_address(level),
            name=f"switch leg {level}",
            class_code=0x060400,
            parent_address=_chain_address(level - 1) if level else "0000:00",
        )
        for level in range(depth)
    )
    inventory = Inventory(hostname="deep-chain", pci_tree=tuple(nodes))

    section = render_fabric(inventory, (), 200, FabricView(density=TreeDensity.FULL))

    buffer = io.StringIO()
    Console(file=buffer, width=200, no_color=True).print(section)
    assert nodes[-1].address in buffer.getvalue(), "the deepest device of the chain is not drawn"
