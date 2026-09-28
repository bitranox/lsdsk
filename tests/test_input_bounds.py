"""What a file reaching lsdsk from outside it is allowed to do to it.

A capture handed to ``--replay`` and the store at ``--history-file`` are both
validated against a Pydantic model, but only after the whole file is already in
memory. These tests hold the guard that runs first, and the bounds a capture
that passed it still has to respect once its contents reach a renderer.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import errno
import io
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast, get_args, get_origin, get_type_hints

import annotated_types
import pytest
from pydantic import BaseModel
from rich.console import Console

from lsdsk.adapters.history.store import load_history
from lsdsk.adapters.hw import capture as shared_capture
from lsdsk.adapters.hw.capture import (
    MAX_DEVICE_TEXT,
    MAX_ENCODED_PAYLOAD,
    MAX_PAYLOAD_BYTES,
    CaptureEnvelope,
    CaptureModel,
    DeviceText,
)
from lsdsk.adapters.hw.linux import capture as linux_capture
from lsdsk.adapters.hw.snapshot import load
from lsdsk.adapters.hw.windows import capture as windows_capture
from lsdsk.adapters.render.tree import FabricView, render_fabric
from lsdsk.adapters.textfile import MAX_INPUT_BYTES, read_json_bounded, read_text_bounded
from lsdsk.domain.enums import TreeDensity
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.models import Inventory, PciNode

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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
    with pytest.raises(ConfigurationError, match="Could not read a snapshot at"):
        read_text_bounded(tmp_path / "absent.json", what="a snapshot")


@pytest.mark.os_agnostic
def test_a_path_under_a_regular_file_is_absent_rather_than_merely_unreadable(tmp_path: Path) -> None:
    """A path component that is a file means nothing can exist below it, ever.

    The two answers differ for a caller that has to start fresh: ``load_history``
    treats absent as "no store yet" and unreadable as "a store may be there, do
    not replace it". Read as unreadable, a ``--history-file`` under a regular file
    made ``record`` report that it had left an existing store alone - a sentence
    about a file that cannot exist - and leave 0, so a timer pointed at such a path
    recorded nothing for as long as it ran.

    The control is here rather than in the directory arm below, which cannot be
    one: ``MissingFileError`` SUBCLASSES ``ConfigurationError``, so that arm's
    ``pytest.raises(ConfigurationError)`` passes whichever answer the classifier
    gives. A directory is genuinely there, so it must be refused as unreadable and
    NOT as absent - without that asserted, this arm would still pass if every
    failed read were called absent.
    """
    from lsdsk.domain.errors import MissingFileError

    in_the_way = tmp_path / "not-a-directory"
    in_the_way.write_text("", encoding="utf-8")

    with pytest.raises(MissingFileError, match="Could not read a history store at"):
        read_text_bounded(in_the_way / "store.json", what="a history store")

    with pytest.raises(ConfigurationError) as refused:
        read_text_bounded(tmp_path, what="a history store")
    assert not isinstance(refused.value, MissingFileError), (
        "the control: a directory that is genuinely there was called absent, so the arm above asserts nothing"
    )


@pytest.mark.os_agnostic
def test_a_directory_handed_to_the_reader_is_refused_cleanly(tmp_path: Path) -> None:
    """stat() succeeds on a directory, so the read is what has to refuse it."""
    # The wrapping is the claim: a directory gives IsADirectoryError, and the
    # caller must meet it as this tool's own error rather than a raw OSError.
    with pytest.raises(ConfigurationError, match="Could not read a snapshot at"):
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

    # The control does two jobs. It proves the run produces output at all, and
    # it proves the PREMISE this test rests on - that nothing else in the
    # process emits an escape byte - so a failure below is the crafted payload
    # getting through rather than Rich colouring its own table. Without it the
    # assertion cannot tell those apart, which is what made it fail under
    # FORCE_COLOR while the stripper was working perfectly.
    clean = cli_runner.invoke(cli, ["disks", "--replay", str(source)], obj=production_factory, color=False)
    assert clean.stdout, "the control produced no output, so it proved nothing"
    assert "\x1b" not in clean.stdout, "this run emits colour of its own, so an escape byte below proves nothing"

    for argv in (["disks"], []):
        result = cli_runner.invoke(cli, [*argv, "--replay", str(crafted)], obj=production_factory, color=False)
        # stdout, not output: the latter carries stderr too, so a coloured log
        # line from anywhere in the process answered for the page.
        assert result.stdout, f"{argv or 'bare'}: no output, so this asserted nothing"
        assert "\x1b" not in result.stdout, f"{argv or 'bare'}: an escape sequence reached the terminal"
        assert "\x07" not in result.stdout, f"{argv or 'bare'}: a bell character reached the terminal"
        # The payload's TEXT is not asserted absent, and must not be: the
        # stripper removes the control characters and keeps the characters, so
        # `]0;PWNED` is a legitimate rendering of a model field that contained
        # it. Asserting "PWNED" is absent passes here only because the model
        # column clips, which is a width away from being wrong.
        # The injected newline is what fabricates a row; the text may still be
        # shown, but it must not have arrived on a line of its own.
        forged = [line for line in result.stdout.splitlines() if line.strip() == "FAKE-ROW"]
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

    Whether the nesting arm still overflows is CPython's choice, not this
    project's, and it moved inside one minor release: 3.14.5 overflows the
    decoder, 3.14.7 parses the document and lets validation refuse it. Both
    refusals are a ``ConfigurationError`` naming this file, which is the whole
    contract, so that is what is asserted - a sentence harvested from whichever
    interpreter happened to be installed is not.
    """
    crafted = tmp_path / "bad.json"
    crafted.write_text(body.format(digits="9" * 20000, deep="[" * 60000 + "]" * 60000), encoding="utf-8")
    # Measured rather than assumed, and per PARAMETRIZATION - the two payloads
    # are refused by different machinery and a pattern taken from one arm fails
    # the other. The digits case trips CPython's integer string-conversion
    # limit, not the JSON grammar; the nesting case overflows the decoder's
    # stack. What both share, and what this test is actually about, is that
    # neither escapes as a raw ValueError: the reader wraps it.
    with pytest.raises(ConfigurationError, match=re.escape(str(crafted))):
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
    with pytest.raises(ConfigurationError, match="far larger than a PCI ID database ever is"):
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
        # The errno is the claim, not the OS's wording: ELOOP is O_NOFOLLOW
        # refusing to open the link, and any other OSError here would mean the
        # fallback failed for an unrelated reason while the test read as green.
        with pytest.raises(OSError) as refusal:
            save(capture, link)
    finally:
        tmp_path.chmod(0o700)
    assert refusal.value.errno == errno.ELOOP, refusal.value
    assert victim.read_text(encoding="utf-8") == "MUST-SURVIVE", "the fallback followed a symlink"


@pytest.mark.os_posix
def test_the_in_place_write_refuses_a_symlink_where_the_kernel_will_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows has no O_NOFOLLOW, so the flag contributed 0 and the link was followed.

    The docstring claimed the refusal without qualifying the platform, which is
    the shape that hides a missing guard: the POSIX test above passes, and
    nothing asks what happens where the flag does not exist.

    os.O_NOFOLLOW is deleted rather than the code branched on a platform string,
    so this runs the SAME path a Windows caller reaches - the attribute's
    absence IS the condition. It is a stdlib constant, which is the external
    edge monkeypatching is for. Driven through ``save`` like its sibling, with
    the same unwritable directory forcing the fallback, so what is proved is
    what a caller gets rather than what a private function does.

    The guarantee there is weaker and the test says which one: a check-then-open,
    so this proves the refusal happens, not that it is atomic.
    """
    from lsdsk.adapters.hw.snapshot import save

    capture: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}}
    victim = tmp_path / "victim.txt"
    victim.write_text("MUST-SURVIVE", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(victim)

    monkeypatch.delattr(os, "O_NOFOLLOW", raising=True)
    assert not hasattr(os, "O_NOFOLLOW"), "the control: the flag is still there, so the POSIX path ran"

    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError) as refusal:
            save(capture, link)
    finally:
        tmp_path.chmod(0o700)

    assert refusal.value.errno == errno.ELOOP, refusal.value
    assert victim.read_text(encoding="utf-8") == "MUST-SURVIVE", "the fallback followed the symlink"


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

    ``--replay <(ssh host cat capture.json)`` hands this tool a FIFO on
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


#: A capture with one storage controller and one drive behind it, written as
#: TEXT rather than dumped, because the point of these arms is a key that
#: appears twice and no JSON writer will produce one.
_CONTROLLER = (
    '"0000:03:00.0": {"class": "0x010700", "device": "0x00e6", "vendor": "0x1000", '
    '"driver": "mpt3sas", "path": "/sys/devices/pci0000:00/0000:03:00.0"}'
)
_GRAPHICS = (
    '"0000:03:00.0": {"class": "0x030000", "device": "0x1234", "vendor": "0x10de", '
    '"path": "/sys/devices/pci0000:00/0000:03:00.0"}'
)
_BLOCK = '"sda": {"size": "2048", "device_path": "/sys/devices/pci0000:00/0000:03:00.0/host0/target0:0:0/0:0:0:0"}'


def _capture_text(*, pci: str, block: str, trailer: str = "") -> str:
    """One snapshot as text, with whatever the arm needs repeated inside it."""
    return (
        '{"schema": 2, "platform": "linux", "hostname": "crafted", "kernel": "6.1.0", '
        f'"pci": {{{pci}}}, "block": {{{block}}}{trailer}}}'
    )


@pytest.mark.os_agnostic
def test_a_capture_that_names_one_pci_address_twice_is_refused(tmp_path: Path) -> None:
    """A repeated key resolves last-writer-wins, and the loser leaves no trace.

    `fabric._keyed_by_address` exists to stop exactly this loss one layer
    further in, because "the device that vanished took its disks' controller
    with it" - and it runs after `json.loads` has already resolved the repeat.
    Measured on the `linux-sas-hba` capture with a graphics device repeating the
    SAS HBA's address: the machine reads `4 controllers` where it has 5, ten
    drives move to "not attached to a known controller", one finding disappears,
    and the fabric grows a third root complex that does not exist. Exit 0
    throughout, nothing on stderr.
    """
    crafted = tmp_path / "duplicate.json"
    crafted.write_text(_capture_text(pci=f"{_CONTROLLER}, {_GRAPHICS}", block=_BLOCK), encoding="utf-8")

    with pytest.raises(ConfigurationError) as refusal:
        load(crafted)

    assert "0000:03:00.0" in str(refusal.value), "the refusal does not name the key that was repeated"


@pytest.mark.os_agnostic
def test_a_capture_that_names_one_whole_section_twice_is_refused(tmp_path: Path) -> None:
    """The severe shape: a repeated SECTION key replaces every entry at once.

    Measured on the `linux-sas-hba` capture with a second, empty `block`
    section: every drive vanishes, the page reports `1 hint` where the control
    reports 7 warnings and 6 hints including a drive carrying 99,345 interface
    CRC errors, and `lsdsk health` exits 0 rather than 1. A tool whose exit code
    is its verdict cannot let a file edit turn "something is wrong here" into
    "nothing is".
    """
    crafted = tmp_path / "duplicate-section.json"
    crafted.write_text(_capture_text(pci=_CONTROLLER, block=_BLOCK, trailer=', "block": {}'), encoding="utf-8")

    with pytest.raises(ConfigurationError) as refusal:
        load(crafted)

    assert "block" in str(refusal.value), "the refusal does not name the section that was repeated"


@pytest.mark.os_agnostic
def test_the_same_capture_without_a_repeated_key_still_loads(tmp_path: Path) -> None:
    """The control both arms above need.

    A guard that refused every capture would pass them and say nothing, and
    this fixture is the one they are built from, so it fails if the refusal is
    reaching anything more than the repeat.
    """
    ordinary = tmp_path / "ordinary.json"
    ordinary.write_text(_capture_text(pci=_CONTROLLER, block=_BLOCK), encoding="utf-8")

    inventory = load(ordinary)

    assert [controller.address for controller in inventory.controllers] == ["0000:03:00.0"]
    assert [disk.node for disk in inventory.disks] == ["sda"]


@pytest.mark.os_agnostic
def test_a_history_store_that_names_one_key_twice_is_refused(tmp_path: Path) -> None:
    """The counter store is read back through the same shape, and is not rebuildable.

    A capture can be taken again from the hardware; this file is the only record
    of what the drives used to say, so a value silently replaced by a later one
    under the same key is worse here than in a capture. The two readers are
    siblings line for line, down to the comment above their handlers, so the
    guard belongs at both or it is one edit from being absent at one.
    """
    recorded = '[{"identity": "naa.1", "model": "a drive", "samples": [{"power_on_hours": 100}]}]'
    store = tmp_path / "history.json"
    store.write_text(f'{{"schema": 1, "hostname": "crafted", "series": {recorded}, "series": []}}', encoding="utf-8")

    with pytest.raises(ConfigurationError) as refusal:
        load_history(store, hostname="crafted")

    assert "series" in str(refusal.value), "the refusal does not name the key that was repeated"


#: The one module allowed to turn a file from outside this tool into JSON.
_JSON_PARSE_HOME = "textfile.py"

#: The calls that parse a whole document. `orjson.loads` in the config
#: overrides is deliberately not here: it parses one value the user typed on
#: the command line, not a document read from a file, so a key repeated in it
#: substitutes the caller's own text and there is no second party to mislead.
_DOCUMENT_PARSERS = ("loads", "load", "model_validate_json")


def _document_parse_sites() -> list[str]:
    """Every call in the source that parses a whole JSON document."""
    found: list[str] = []
    source_root = Path(__file__).parent.parent / "src" / "lsdsk"
    for module in sorted(source_root.rglob("*.py")):
        if module.name == _JSON_PARSE_HOME:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "model_validate_json" and not (
                node.func.attr in _DOCUMENT_PARSERS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                continue
            found.append(f"{module.relative_to(source_root)}:{node.lineno} {node.func.attr}")
    return found


@pytest.mark.os_agnostic
def test_only_one_module_turns_a_file_from_outside_into_json() -> None:
    """The repeated-key guard is a property of WHERE parsing happens.

    Three sites parsed a document before this was written, two through
    ``json.loads`` and one through Pydantic's own parser, and all three resolved
    a repeated key last-writer-wins. Guarding the ones that existed leaves the
    next one to be written unguarded, and nothing would say so: a repeat is
    silent by construction. So the shape is held instead of the instances.

    Asserted over the call graph rather than the text, because a comment naming
    ``json.loads`` is not a call and must not fail this.
    """
    assert _document_parse_sites() == [], (
        f"these parse a document outside the one module that refuses a repeated key: {_document_parse_sites()}"
    )


#: The largest sector count the kernel's own ``sector_t`` can hold. A capture
#: naming more than this did not come from a block device.
_MAX_SECTORS = 2**64 - 1


def _capture_with(*, size: str = "1024", ports_implemented: int = 0x3) -> str:
    """A Linux capture of one AHCI controller and one drive behind it."""
    return (
        '{"schema": 2, "platform": "linux", "hostname": "crafted", "kernel": "6.1.0", '
        '"pci": {"0000:00:17.0": {"class": "0x010601", "device": "0x1d02", "vendor": "0x8086", '
        '"driver": "ahci", "path": "/sys/devices/pci0000:00/0000:00:17.0", '
        f'"ahci": {{"capability": 3878747973, "ports_implemented": {ports_implemented}}}}}}}, '
        f'"block": {{"sda": {{"size": "{size}", "device_path": '
        '"/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0"}}}'
    )


@pytest.mark.os_agnostic
def test_a_ports_bitmap_wider_than_the_register_is_refused(tmp_path: Path) -> None:
    """The port count is DERIVED, so an unbounded bitmap is a figure lsdsk invents.

    AHCI's ports-implemented register is 32 bits wide, and the count comes from
    counting its set bits. Measured with a bitmap of 14,000 bits:
    ``lsdsk controllers`` reports ``14000`` ports and ``13999`` free, exit 0,
    on a tool whose first rule is never to report what it did not measure. It is
    not the capture's own claim being passed on, which is what every other wrong
    value in a capture is - it is arithmetic on a value the register cannot hold.
    """
    crafted = tmp_path / "wide-bitmap.json"
    crafted.write_text(_capture_with(ports_implemented=(1 << 14000) - 1), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="ports_implemented"):
        load(crafted)


@pytest.mark.os_agnostic
def test_a_bitmap_filling_the_register_is_still_read(tmp_path: Path) -> None:
    """The control the arm above needs, at the largest value the register holds.

    A bound one too tight would refuse a controller declaring all 32 ports and
    nothing would say so, because the arm above passes either way.
    """
    crafted = tmp_path / "full-bitmap.json"
    crafted.write_text(_capture_with(ports_implemented=0xFFFFFFFF), encoding="utf-8")

    inventory = load(crafted)

    assert inventory.controllers[0].port_count == 32, "the implemented-port count is not the bitmap's own"


@pytest.mark.os_agnostic
def test_a_sector_count_no_block_device_could_hold_reports_no_size(tmp_path: Path) -> None:
    """A capacity is text on Linux, so the builder answers rather than the model.

    Two measured consequences, both from the same missing bound. A 320-digit
    sector count made ``lsdsk disks`` exit 1 printing a bare
    ``OverflowError: int too large to convert to float`` while ``controllers``,
    ``health`` and ``findings`` all exited 0 on the same capture. Well below
    that, a 40-digit one printed ``4547473508864641327721086976PiB`` as a
    measurement.

    Read as "not measured" rather than refused, because a capture leaves values
    as the text the platform published and decoding that text is the builder's
    tolerant job - the same answer it already gives for a size of ``Unknown``.
    """
    crafted = tmp_path / "huge-size.json"
    crafted.write_text(_capture_with(size="9" * 320), encoding="utf-8")

    inventory = load(crafted)

    assert inventory.disks[0].size_bytes is None, "a sector count no device could report became a capacity"


@pytest.mark.os_agnostic
def test_the_largest_sector_count_a_block_device_could_hold_is_still_a_size(tmp_path: Path) -> None:
    """The control for the arm above, at the ceiling rather than past it."""
    crafted = tmp_path / "largest-size.json"
    crafted.write_text(_capture_with(size=str(_MAX_SECTORS)), encoding="utf-8")

    inventory = load(crafted)

    assert inventory.disks[0].size_bytes == _MAX_SECTORS * 512


@pytest.mark.os_agnostic
def test_a_windows_length_wider_than_the_api_that_reports_it_is_refused(tmp_path: Path) -> None:
    """An unbounded length does not print oddly, it DELETES columns.

    ``IOCTL_DISK_GET_LENGTH_INFO`` answers in a ``LARGE_INTEGER``, so a length
    past a signed 64-bit one was never read from a disk. Measured with a
    301-digit length on the ``windows-ahci`` capture, rendered at 400 columns:
    the disks table lost ``serial``, ``firmware``, ``size`` and ``kind``, so the
    drive's identity disappeared rather than the number looking wrong. It is the
    same failure the WWN column is width-capped for, arriving through an integer.
    """
    crafted = tmp_path / "wide-length.json"
    crafted.write_text(
        json.dumps(
            {
                "schema": 2,
                "platform": "win32",
                "hostname": "crafted",
                "kernel": "10.0.19045",
                "pci": {},
                "disks": {"one": {"node": "PhysicalDrive0", "size_bytes": 10**300}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="size_bytes"):
        load(crafted)


#: The one integer a capture carries that is NOT a reading, and the one that
#: must stay unbounded: the loader compares it against the range this version
#: reads so it can say a snapshot was written by a newer lsdsk. Bounded at the
#: model, that clear answer would become "this is not a snapshot".
_NOT_A_READING = "schema_version"


def _capture_models() -> list[type[CaptureModel]]:
    """Every model a capture is parsed into, found rather than listed.

    Walked from the modules so a model added later is covered: a list written
    here could only ever hold the models somebody remembered to add to it.
    """
    modules = (shared_capture, linux_capture, windows_capture)
    found = {
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, CaptureModel) and value is not CaptureModel
    }
    return sorted(found, key=lambda model: model.__name__)


def _unbounded_int_fields() -> list[str]:
    """Integer fields on a capture model with no upper bound."""
    loose: list[str] = []
    for model in _capture_models():
        for name, field in model.model_fields.items():
            if name == _NOT_A_READING or (int not in get_args(field.annotation) and field.annotation is not int):
                continue
            if not any(isinstance(entry, annotated_types.Le) for entry in field.metadata):
                loose.append(f"{model.__name__}.{name}")
    return sorted(set(loose))


@pytest.mark.os_agnostic
def test_every_integer_a_capture_carries_is_bounded_by_its_own_source() -> None:
    """A width is a fact about the register or the API, so it belongs at the parse.

    Every one of these is read from something with a width - a 32-bit AHCI
    register, a 4-bit PCIe field, a ``c_short``, a ``LARGE_INTEGER`` - and a
    value wider than its own source was never read from hardware. Two reached
    figures the tool then stated as measurements: a ports bitmap of 14,000 bits
    was counted into "14000 ports, 13999 free", and a length of 10**300 pushed
    four columns off the disks table so a drive's serial and firmware vanished.

    Asserted over every field rather than the two that were found, because the
    next one added would be unbounded and nothing would say so until a capture
    exercised it.
    """
    assert _unbounded_int_fields() == [], (
        f"these carry no width from the source they are read from: {_unbounded_int_fields()}"
    )


@dataclasses.dataclass(frozen=True)
class _PlantedNestedDataclass:
    """Shaped like `VirtualizationEvidence`: a plain dataclass with one loose field.

    Planted so a control can prove the walker steps INTO a nested dataclass
    rather than stopping at the field that names it - which is exactly the
    shape `VirtualizationEvidence` has as `LinuxCapture.environment` and
    `WindowsCapture.environment`.
    """

    loose: str = ""
    bounded: DeviceText = ""


class _PlantedNestedModel(BaseModel):
    """The same control, for a nested `BaseModel` that is not itself walked directly."""

    loose: str = ""
    bounded: DeviceText = ""


def _loose_strings(annotation: Any, metadata: Sequence[Any] = (), *, _seen: frozenset[type] = frozenset()) -> int:
    """How many `str` positions in an annotation carry no maximum length.

    Walks every place a string can sit in a capture - an optional, a tuple, a
    mapping's KEY as well as its value - because a bound on the value alone
    leaves the key free to carry the same payload. It also DESCENDS into a
    nested type's own fields (a pydantic ``BaseModel`` or a plain dataclass),
    rather than stopping at the field that names it: a capture model's field
    can be typed as a dataclass built for pure classification (no bound of its
    own), and a walker that only reads generic type arguments sees no `str`
    there at all, because a plain class has none. ``_seen`` guards a type
    that refers to itself, which nothing here does today but which the next
    nested type might.
    """
    if annotation is str:
        return 0 if any(isinstance(entry, annotated_types.MaxLen) for entry in metadata) else 1
    if get_origin(annotation) is Annotated:
        inner, *extras = get_args(annotation)
        found = [*metadata, *extras, *(item for extra in extras for item in getattr(extra, "metadata", ()))]
        return _loose_strings(inner, found, _seen=_seen)
    if isinstance(annotation, type) and annotation not in _seen:
        # An explicit `type[object]` variable, not `annotation` itself, is what
        # is handed to `dataclasses`/`typing` below: narrowing `Any` still
        # leaves it partially unknown under strict, and this is where that
        # gets resolved rather than waived.
        checked: type[object] = annotation
        nested = _seen | {checked}
        if issubclass(checked, BaseModel):
            return sum(
                _loose_strings(field.annotation, field.metadata, _seen=nested)
                for field in checked.model_fields.values()
            )
        if dataclasses.is_dataclass(checked):
            hints = get_type_hints(checked, include_extras=True)
            return sum(
                _loose_strings(hints.get(field.name, field.type), _seen=nested) for field in dataclasses.fields(checked)
            )
    return sum(_loose_strings(argument, _seen=_seen) for argument in get_args(annotation) if argument is not Ellipsis)


def _unbounded_text_fields() -> list[str]:
    """Text fields on a capture model with no maximum length, in any position."""
    return sorted(
        f"{model.__name__}.{name}"
        for model in _capture_models()
        for name, field in model.model_fields.items()
        if _loose_strings(field.annotation, field.metadata)
    )


@pytest.mark.os_agnostic
def test_every_string_a_capture_carries_is_bounded() -> None:
    """The integer rule above, for text: one field inside the file cap is not bounded by it.

    `pci_names` was a plain `dict[str, str]` while every field beside it was
    `DeviceText`, and a resolved name is looked up once per device that shares
    its id. Measured: 500 devices sharing one id named by a 1 MB string took
    27 s and 569 MB to draw, against 0.46 s and 50 MB for a short name, and it
    grows with the product, so a few megabytes of capture reached gigabytes.
    Disk models, serials, firmware and board names were loose the same way.

    Asserted over every field and every position in it, so the next field added
    as a bare `str` fails here rather than waiting for a capture to find it.
    """
    loose = _unbounded_text_fields()
    assert loose == [], f"these carry text with no maximum length: {loose}"


@pytest.mark.os_agnostic
def test_the_string_guard_sees_a_loose_key_and_a_loose_optional() -> None:
    """The control: the walker must answer both ways, in every position it claims."""
    assert _loose_strings(dict[str, DeviceText]) == 1, "an unbounded mapping KEY went unseen"
    assert _loose_strings(str | None) == 1, "an unbounded optional went unseen"
    assert _loose_strings(tuple[str, ...]) == 1, "an unbounded tuple member went unseen"
    assert _loose_strings(dict[DeviceText, DeviceText | None]) == 0, "a bounded mapping was reported loose"
    assert _loose_strings(tuple[DeviceText, ...]) == 0, "a bounded tuple was reported loose"


@pytest.mark.os_agnostic
def test_the_string_guard_descends_into_a_nested_dataclass_or_model() -> None:
    """The control for the gap `VirtualizationEvidence` fell through.

    A field typed as a plain class - a dataclass built for pure classification,
    or a `BaseModel` embedded rather than walked directly - carries no `str` as
    far as `get_args` is concerned, because a plain class is not generic. The
    walker has to step INTO such a type's own fields, or a bare `str` field on
    it is invisible to every capture-model sweep, which is exactly how
    `VirtualizationEvidence.cgroup` and its siblings went unbounded.
    """
    assert _loose_strings(_PlantedNestedDataclass) == 1, "a loose str inside a nested dataclass went unseen"
    assert _loose_strings(_PlantedNestedModel) == 1, "a loose str inside a nested model went unseen"
    assert _loose_strings(_PlantedNestedDataclass | None) == 1, "an optional nested dataclass went unseen"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("where", ["value", "key"])
def test_a_device_name_longer_than_any_device_publishes_is_refused(tmp_path: Path, where: str) -> None:
    """The end-to-end arm: the oversized name is refused at load, not drawn."""
    long_text = "A" * (MAX_DEVICE_TEXT + 1)
    names = {"8086:a182": long_text} if where == "value" else {long_text: "Intel"}
    crafted = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}, "pci_names": names}
    path = tmp_path / "huge-name.json"
    path.write_text(json.dumps(crafted), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="at most 4096"):
        load(path)


@pytest.mark.os_agnostic
def test_a_huge_virtualization_field_inside_the_environment_section_is_refused(tmp_path: Path) -> None:
    """The end-to-end arm for the nested-dataclass gap: `environment.cgroup` is bounded too.

    `VirtualizationEvidence` is a plain dataclass, not a `CaptureModel`, and it
    sits behind `LinuxCapture.environment` and `WindowsCapture.environment`.
    Before it carried the bounded `DeviceText` type on its own fields, a
    capture whose `environment.cgroup` ran to millions of characters parsed
    and was accepted whole.
    """
    crafted = {
        "schema": 2,
        "platform": "linux",
        "hostname": "box",
        "kernel": "x",
        "pci": {},
        "environment": {"cgroup": "A" * (MAX_DEVICE_TEXT + 1)},
    }
    path = tmp_path / "huge-environment.json"
    path.write_text(json.dumps(crafted), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="at most 4096"):
        load(path)


def _crafted_with_payload(encoded: str) -> dict[str, Any]:
    """A minimal capture whose one ATA blob carries `encoded` as its IDENTIFY page."""
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "box",
        "kernel": "x",
        "pci": {},
        "ata": {"sda": {"identify": encoded}},
    }


@pytest.mark.os_agnostic
def test_a_payload_encoding_exactly_the_stated_ceiling_is_accepted(tmp_path: Path) -> None:
    """`MAX_PAYLOAD_BYTES` names RAW bytes, and the ceiling it implies is reachable, not just close.

    Nothing asserted that `MAX_ENCODED_PAYLOAD` is the ceiling the comment
    claims - only that SOME `MaxLen` exists. A raw page of exactly the stated
    size must still parse whole: this is the accepted arm.
    """
    encoded = base64.b64encode(b"\xab" * MAX_PAYLOAD_BYTES).decode("ascii")
    assert len(encoded) == MAX_ENCODED_PAYLOAD, "the fixture no longer sits exactly at the stated ceiling"

    path = tmp_path / "at-the-ceiling.json"
    path.write_text(json.dumps(_crafted_with_payload(encoded)), encoding="utf-8")

    parsed = load(path)

    assert isinstance(parsed, Inventory)


@pytest.mark.os_agnostic
def test_a_payload_past_the_encoded_ceiling_is_refused(tmp_path: Path) -> None:
    """The refused arm: an encoded page wider than `MAX_ENCODED_PAYLOAD` is not a capture lsdsk accepts.

    Three raw bytes past `MAX_PAYLOAD_BYTES` is the smallest input that
    actually WIDENS the base64 text, because of the slack the control below
    holds: a raw page one or two bytes over the stated ceiling still encodes
    to the SAME length, so it would pass here for the wrong reason.
    """
    encoded = base64.b64encode(b"\xab" * (MAX_PAYLOAD_BYTES + 3)).decode("ascii")
    assert len(encoded) > MAX_ENCODED_PAYLOAD, "the fixture no longer exceeds the stated ceiling"

    path = tmp_path / "past-the-ceiling.json"
    path.write_text(json.dumps(_crafted_with_payload(encoded)), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=f"at most {MAX_ENCODED_PAYLOAD}"):
        load(path)


@pytest.mark.os_agnostic
def test_the_encoded_ceiling_admits_at_most_two_raw_bytes_of_quantisation_slack() -> None:
    """The control the two tests above rely on: base64 groups three raw bytes into four characters.

    A raw payload one or two bytes past `MAX_PAYLOAD_BYTES` encodes to the
    IDENTICAL length as one exactly at it, because base64 only grows its
    output once a full three-byte group is complete. Three bytes past is
    where the encoded length first actually exceeds `MAX_ENCODED_PAYLOAD`,
    which is why the refused-arm test above uses `+3` rather than `+1`.
    """
    at_ceiling = len(base64.b64encode(b"\xab" * MAX_PAYLOAD_BYTES))
    one_over = len(base64.b64encode(b"\xab" * (MAX_PAYLOAD_BYTES + 1)))
    two_over = len(base64.b64encode(b"\xab" * (MAX_PAYLOAD_BYTES + 2)))
    three_over = len(base64.b64encode(b"\xab" * (MAX_PAYLOAD_BYTES + 3)))

    assert at_ceiling == MAX_ENCODED_PAYLOAD
    assert one_over == MAX_ENCODED_PAYLOAD, "one raw byte of slack no longer widens nothing"
    assert two_over == MAX_ENCODED_PAYLOAD, "two raw bytes of slack no longer widen nothing"
    assert three_over > MAX_ENCODED_PAYLOAD, "three raw bytes over no longer crosses the ceiling"


@pytest.mark.os_agnostic
def test_the_version_a_capture_declares_is_deliberately_not_bounded() -> None:
    """The control for the rule above, which would otherwise read as complete.

    A rule with one exception is a rule whose exception can be quietly widened.
    This pins that the exception is exactly one field and that it is the one
    named, so removing the bound from a reading cannot be excused by it.
    """
    envelope: dict[str, Any] = {
        "schema": 10**300,
        "platform": "linux",
        "hostname": "crafted",
        "kernel": "6.1.0",
        "pci": {},
        "block": {},
    }

    parsed = CaptureEnvelope.model_validate(envelope)

    assert parsed.schema_version == 10**300, "the version a capture declares is no longer readable"
    assert _NOT_A_READING in CaptureEnvelope.model_fields


#: Every capture committed here that carries device text worth salting.
_SALTED_CAPTURES = ("linux-sas-hba", "linux-nvme-board", "windows-ahci")

#: The eight section commands, plus the default page as the empty argv.
_SALTED_VIEWS = ("topology", "controllers", "disks", "health", "smart", "slots", "trend", "findings", "")

#: Control characters from every range the sanitiser covers, plus a newline,
#: which is what forges a table row. Each is a separate class of damage: ESC
#: recolours and retitles, BEL sounds, NUL and DEL corrupt a parser, and the C1
#: range is a second escape encoding some terminals still honour.
_PAYLOAD = "\x1b[31m\x07\x00\x7f\x9b\nFAKE-ROW"

#: A visible marker so a reader of a failure can see WHICH value leaked.
_MARK = "SALTMARK"

#: Keys whose value is structural rather than device text. ``platform`` selects
#: the builder, so salting it does not test the sanitiser, it tests the dispatch.
_STRUCTURAL_KEYS = frozenset({"platform"})

#: Cells where a salted capture reaches nothing, MEASURED rather than assumed,
#: and asserted below in the direction that makes the exclusion self-cancelling:
#: if one of these ever starts carrying device text, the arm fails and says to
#: promote it rather than silently covering nothing.
#:
#: Keyed by CAPTURE as well as view and format, because whether a cell carries
#: anything is a fact about the machine in the capture and not about the view
#: alone. ``trend`` in human form prints an explanation naming no device at all
#: when no history has been recorded, which is every run of this suite - the
#: autouse fixture gives each test its own empty state directory. It now also
#: ends with the line accounting for its own exit code, which a capture with an
#: actionable finding gets and ``windows-ahci``, whose drives are clean, does
#: not. So the two captures that do carry findings have a real arm here now, and
#: this is the one that still has nothing: the exclusion cancelled itself for
#: the others exactly as its own sentence says it should.
_CARRIES_NO_DEVICE_TEXT = frozenset({("windows-ahci", "trend", "human")})


def _salted(node: object, key: str | None = None) -> object:
    """The same capture with every device-text string carrying the payload."""
    if isinstance(node, dict):
        entries = cast("dict[str, object]", node)
        return {name: _salted(value, name) for name, value in entries.items()}
    if isinstance(node, list):
        return [_salted(value) for value in cast("list[object]", node)]
    if isinstance(node, str) and key not in _STRUCTURAL_KEYS:
        return f"{node}{_MARK}{_PAYLOAD}"
    return node


def _decoded_strings(node: object) -> list[str]:
    """Every string a JSON document carries, keys included, after decoding.

    The decoded value is the only honest surface for the JSON arm: the encoder
    escapes a control character, so counting raw bytes in the document reads
    zero whether the sanitiser ran or not, and the arm passes against the
    defect it exists to catch.
    """
    if isinstance(node, dict):
        entries = cast("dict[object, object]", node)
        keys = [name for name in entries if isinstance(name, str)]
        return keys + [text for value in entries.values() for text in _decoded_strings(value)]
    if isinstance(node, list):
        return [text for value in cast("list[object]", node) for text in _decoded_strings(value)]
    return [node] if isinstance(node, str) else []


def _control_characters(text: str, *, output_format: str) -> list[str]:
    """Which control characters a reader would actually meet on this surface.

    A newline is legitimate in human output, where it separates lines, and is
    NOT legitimate inside a decoded JSON value, where it is the row-forging
    payload arriving intact.
    """
    if output_format == "json":
        if not text.strip():
            return []
        values = _decoded_strings(json.loads(text))
        return [ch for value in values for ch in value if _is_control(ch)]
    return [ch for ch in text if _is_control(ch) and ch != "\n"]


def _is_control(ch: str) -> bool:
    point = ord(ch)
    return point < 0x20 or point == 0x7F or 0x80 <= point < 0xA0


@pytest.mark.os_agnostic
@pytest.mark.parametrize("capture_name", _SALTED_CAPTURES)
@pytest.mark.parametrize("view", _SALTED_VIEWS)
@pytest.mark.parametrize("output_format", ["human", "json"])
def test_no_control_character_reaches_any_view_from_a_salted_capture(
    capture_name: str,
    view: str,
    output_format: str,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """The invariant over the whole matrix, not the two views it was raised on.

    The guard used to be asserted on ``disks`` and ``controllers`` in human form
    with four fields planted by hand. The claim is that NO control character
    reaches ANY output surface, and the fields a capture carries are chosen by
    the hardware, so the arm has to be every view, both formats, every string.

    Both streams are read. A warning naming the machine goes to stderr, so
    checking stdout alone leaves a surface the payload demonstrably reaches.
    """
    from lsdsk.adapters.cli import cli

    source = Path(__file__).parent / "fixtures" / "hw" / f"{capture_name}.json"
    crafted = tmp_path / f"salted-{capture_name}.json"
    crafted.write_text(json.dumps(_salted(json.loads(source.read_text(encoding="utf-8")))), encoding="utf-8")

    argv = [*([view] if view else []), *(["--format", "json"] if output_format == "json" else [])]
    if not view and output_format == "json":
        pytest.skip("the default page has no JSON form; lsdsk snapshot is its machine-readable one")
    prefix = ["--no-record", "--history-file", str(tmp_path / "absent.json")]

    def run(path: Path) -> Any:
        return cli_runner.invoke(cli, [*prefix, *argv, "--replay", str(path)], obj=production_factory, color=False)

    clean, dirty = run(source), run(crafted)
    cell = f"{capture_name}/{view or 'bare'}/{output_format}"
    assert dirty.exit_code in (0, 1), f"{cell}: the run failed with {dirty.exit_code}, so it rendered nothing"
    assert dirty.stdout.strip() or dirty.stderr.strip(), f"{cell}: produced no output at all"

    reached = (clean.stdout, clean.stderr) != (dirty.stdout, dirty.stderr)
    if (capture_name, view, output_format) in _CARRIES_NO_DEVICE_TEXT:
        assert not reached, f"{cell}: now carries device text, so give it a real arm instead of an exclusion"
        return
    assert reached, f"{cell}: salting changed nothing here, so this arm asserts nothing"

    for stream, text in (("stdout", dirty.stdout), ("stderr", dirty.stderr)):
        fmt = output_format if stream == "stdout" else "human"
        leaked = _control_characters(text, output_format=fmt)
        assert not leaked, f"{cell}: {len(leaked)} control characters reached {stream}: {[hex(ord(c)) for c in leaked]}"


@pytest.mark.os_agnostic
def test_a_device_identifier_longer_than_any_device_publishes_is_refused(tmp_path: Path) -> None:
    """The file bound is not a bound on what one FIELD can do downstream.

    A PCI vendor or device identifier is four hex characters as sysfs writes it
    and a short name once resolved, but the capture models declared plain
    `str`, so a single value inside the 64 MB file ceiling round-tripped
    through the integer parse, the hex re-format and then the per-character
    control-stripping generator. Measured at 1, 10, 25 and 50 MB: a consistent
    12 to 13x memory multiplier, 664880 KB RSS against a 46440 KB baseline,
    with 26,235,615 calls into the generator - so one within-cap field could
    reach roughly 800 MB, against a module whose stated intent is an immediate
    refusal rather than a machine that swaps itself to death.

    Refused at the model, where the shape is already declared, and refused as a
    configuration error like every other capture this version cannot read.
    """
    crafted = {
        "schema": 2,
        "platform": "linux",
        "hostname": "box",
        "kernel": "x",
        "pci": {"0000:00:1f.2": {"vendor": "8" * (MAX_DEVICE_TEXT + 1), "device": "a182"}},
    }
    path = tmp_path / "huge-field.json"
    path.write_text(json.dumps(crafted), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="at most 4096"):
        load(path)

    # The control: the same capture with a value of a length a device really
    # publishes still loads, so the refusal is the length and not the shape.
    crafted["pci"] = {"0000:00:1f.2": {"vendor": "8086", "device": "a182"}}
    ordinary = tmp_path / "ordinary.json"
    ordinary.write_text(json.dumps(crafted), encoding="utf-8")
    assert load(ordinary).hostname == "box"


@pytest.mark.os_agnostic
def test_the_counter_store_cleans_the_text_it_carries_like_every_other_domain_field() -> None:
    """The store is a file the caller points at, so its text is chosen elsewhere.

    `Disk.model` and `Inventory.hostname` are `DeviceText` and strip, because
    `domain/text.py` moved the cleaning onto the FIELD so that "a new field has
    nothing to remember". `history.py` is the file that forgot: four of its
    strings were plain `str` while carrying exactly the same kind of value, and
    `DiskSeries.model` is documented as being for display.
    """
    from lsdsk.domain.history import DiskSeries

    assert "\x1b" not in DiskSeries(identity="naa.1\x1b[31m", model="Model").identity
    assert "\x1b" not in DiskSeries(identity="naa.1", model="Model\x1b]0;retitled\x07").model


@pytest.mark.os_agnostic
def test_the_store_s_hostname_and_timestamp_are_cleaned_too() -> None:
    """The other two fields of the same shape, and the machine name is drawn."""
    from lsdsk.domain.history import History, Sample

    assert "\x1b" not in History(hostname="box\x1b[2J").hostname
    assert "\x1b" not in Sample(power_on_hours=1, captured_at="2026-01-01\x1b[0m").captured_at


@pytest.mark.os_agnostic
def test_diagnosing_a_machine_at_the_input_ceiling_does_not_cost_minutes() -> None:
    """A capture under the documented size limit cannot burn the CPU for minutes.

    `diagnose` used to be quadratic in disks times controllers, from two
    independent searches: the hunt for a faster free port rebuilt the rate of
    every controller from the whole disk list for every drive, and the hunt for
    a swap partner walked every disk to reject it. Neither is reachable from
    real hardware, where 500 drives on 20 controllers is sub-millisecond. Both
    are reachable from one `--replay` file: `textfile.py` records about 8 KB per
    drive, so the 64 MB `MAX_INPUT_BYTES` admits several thousand.

    Measured here on this shape, 3200 drives on 200 controllers: 58.1 seconds
    before, 0.16 after. The ceiling below is two orders of magnitude above the
    measurement and one below the old cost, so it separates the two on any
    machine that can run the suite at all rather than pinning a speed.

    The shape matters as much as the size. Every port has to be the SAME speed,
    or the search returns on the first faster controller it meets and never
    reaches the cost this is about; and each drive has to sit AT its port's
    maximum and below its own, or the rule reports the slow link and returns
    before the search starts.

    It guards the AGGREGATE, not any one index: reverting the per-controller
    rate map alone leaves 1.2 seconds here and this still passes, because the
    disk index absorbs it. Mutating both together is what kills it, and that is
    the arm this was proved on.
    """
    # The one test in this module that measures anything.
    import time

    from lsdsk.domain.diagnostics import diagnose
    from lsdsk.domain.models import Controller, Disk, InterfaceLink, Inventory

    controllers = tuple(
        Controller(address=f"0000:{index:02x}:00.0", name="c", port_count=16, ports_used=1) for index in range(200)
    )
    disks = tuple(
        Disk(
            node=f"sd{index}",
            path=f"/dev/sd{index}",
            model="m",
            controller_address=f"0000:{index % 200:02x}:00.0",
            link=InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=12.0, port_max_gbps=6.0),
        )
        for index in range(3200)
    )
    machine = Inventory(hostname="h", disks=disks, controllers=controllers)

    started = time.perf_counter()
    findings = diagnose(machine)
    took = time.perf_counter() - started

    # The control: this shape has to REACH the rules, or a fast run would mean
    # only that nothing ran. One finding per drive is what it produces.
    assert len(findings) == len(disks), f"the shape produced {len(findings)} findings, so it missed the rules"
    assert took < 10.0, f"diagnosing {len(disks)} drives on {len(controllers)} controllers took {took:.1f}s"


@pytest.mark.os_agnostic
def test_a_finding_cleans_its_own_text_like_every_other_domain_model() -> None:
    """`Finding` is inside the sanitiser invariant, not beside it.

    `domain/text.py` states the design in its own docstring: cleaning on the
    FIELD is what makes it hold, so a new builder, a new platform or a new field
    has nothing to remember. `Finding` was the one text-carrying domain model
    outside it, and it is the model rendered directly in every view.

    Nothing built today reaches it - every construction interpolates values
    already cleaned upstream - so this guards the NEXT one: a finding built from
    a configuration value, a command-line argument or an exception message. The
    end-to-end salting test cannot see that case, because it salts capture
    fields.
    """
    from lsdsk.domain.enums import Severity
    from lsdsk.domain.models import Finding

    salted = Finding(
        severity=Severity.WARNING,
        subject="/dev/sda\x1b[31m",
        title="a title with \x1b]0;a terminal title\x07 in it",
        detail="two\x00lines\x1b[2J",
        action="\x07do this",
    )

    for field, value in (
        ("subject", salted.subject),
        ("title", salted.title),
        ("detail", salted.detail),
        ("action", salted.action or ""),
    ):
        assert "\x1b" not in value, f"an escape survived in {field}: {value!r}"
        assert "\x00" not in value, f"a NUL survived in {field}: {value!r}"
        assert "\x07" not in value, f"a bell survived in {field}: {value!r}"

    # The control: cleaning keeps the text, it does not blank the field, and an
    # already-clean value passes through unchanged.
    assert "do this" in (salted.action or ""), salted.action
    plain = Finding(severity=Severity.HINT, subject="s", title="t", detail="d", action="a")
    assert (plain.subject, plain.title, plain.detail, plain.action) == ("s", "t", "d", "a")


@pytest.mark.os_linux
def test_a_sysfs_attribute_past_the_ceiling_is_not_read_rather_than_read_whole(tmp_path: Path) -> None:
    """The two sysfs reads are bounded like every other read of foreign data.

    `textfile.py` states the discipline as covering every read of data this tool
    did not write, and these two were the exception: `read_text()` and
    `read_bytes()`, both unbounded, with the blob then base64-encoded into the
    capture and the emitted JSON. Ordinary text attributes are one page, but the
    binary ones are `bin_attribute` files whose content comes from the device.

    It is NOT READ rather than refused: this runs per device on a live scan, and
    one odd attribute must not end the diagnosis of the hardware in front of
    somebody. `None` is what every unreadable attribute already answers.
    """
    from lsdsk.adapters.hw.linux.reader import (
        MAX_SYSFS_BYTES,
        _read_blob,  # pyright: ignore[reportPrivateUsage] - the bound lives on the private reader; remove if it is ever published
        _read_text,  # pyright: ignore[reportPrivateUsage] - likewise
    )

    small = tmp_path / "model"
    small.write_text("a drive\n", encoding="utf-8")
    huge = tmp_path / "vpd"
    huge.write_bytes(b"\xff" * (MAX_SYSFS_BYTES + 1))

    # The control: an ordinary attribute still reads, so a pair of Nones below
    # would not be the bound doing its job but the reader being broken.
    assert _read_text(small) == "a drive"
    assert _read_blob(small) is not None

    assert _read_text(huge) is None, "an oversized attribute was read as text"
    assert _read_blob(huge) is None, "an oversized attribute was base64-encoded into the capture"


@pytest.mark.os_agnostic
def test_a_live_attribute_past_the_capture_bound_is_not_read_rather_than_ending_the_scan(tmp_path: Path) -> None:
    """The reader stops where the capture model does, so a live scan never refuses itself.

    Bounding the capture's text left the reader keeping anything up to a
    megabyte, so one attribute longer than the model allows would have been
    read, then refused on parse, and the whole scan ended at exit 78 over it.
    The control is the longest value the kernel can publish: `sysfs_emit`
    formats into one page, so an attribute is at most 4095 characters plus its
    newline, and every real one must still be kept.
    """
    from lsdsk.adapters.hw.linux.capture import LinuxCapture
    from lsdsk.adapters.hw.linux.reader import read_block

    for node, model in (("sda", "A" * (MAX_DEVICE_TEXT + 1)), ("sdb", "B" * (MAX_DEVICE_TEXT - 1))):
        device = tmp_path / node / "device"
        device.mkdir(parents=True)
        (tmp_path / node / "size").write_text("100\n", encoding="utf-8")
        (device / "model").write_text(f"{model}\n", encoding="utf-8")

    block = read_block(tmp_path)
    parsed = LinuxCapture.model_validate(
        {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}, "block": block}
    )

    assert parsed.block["sda"].device.model is None, "an attribute past the capture bound was kept"
    assert parsed.block["sdb"].device.model == "B" * (MAX_DEVICE_TEXT - 1), "a page-sized attribute was dropped"


@pytest.mark.os_agnostic
def test_the_virtualization_reads_stop_at_the_capture_bound_not_the_megabyte(monkeypatch: pytest.MonkeyPatch) -> None:
    """`read_environment` stores through the capture's own bound, not the wider sysfs one.

    These values reach `LinuxCapture.environment`, and Pydantic parses that
    field into `VirtualizationEvidence` the same way it parses any other
    section, so a value past `MAX_DEVICE_TEXT` must degrade to "not read" at
    the reader - read at the wider `MAX_SYSFS_BYTES` limit and only refused
    later, it would end the whole scan over one file, which is what happened
    to `block.*.device.model` before sweep 6 fixed it there.
    `/proc/self/mountinfo` and `/proc/cpuinfo` are the two reads that are NOT
    stored whole (only a short derived marker is kept), so they keep the
    wider bound and are asserted to stay on it. The seam under test is the
    `limit` `_read_text` is actually called with, read straight from the
    real function's own call rather than re-implemented here.
    """
    from lsdsk.adapters.hw.linux import reader as linux_reader

    calls: list[tuple[Path, int]] = []
    real_text = linux_reader._read_text  # pyright: ignore[reportPrivateUsage] - the seam under test

    def spy_text(path: Path, *, limit: int = linux_reader.MAX_SYSFS_BYTES) -> str | None:
        calls.append((path, limit))
        return real_text(path, limit=limit)

    monkeypatch.setattr(linux_reader, "_read_text", spy_text)

    linux_reader.read_environment()

    seen = dict(calls)
    bounded_paths = {
        Path("/run/systemd/container"),
        Path("/proc/1/cgroup"),
        Path("/sys/class/dmi/id/sys_vendor"),
        Path("/sys/class/dmi/id/product_name"),
        Path("/sys/class/dmi/id/board_vendor"),
        Path("/sys/class/dmi/id/board_name"),
        Path("/sys/hypervisor/type"),
    }
    wide_paths = {Path("/proc/self/mountinfo"), Path("/proc/cpuinfo")}

    assert bounded_paths <= seen.keys(), f"a stored path was never read at all: {bounded_paths - seen.keys()}"
    assert all(seen[path] == MAX_DEVICE_TEXT for path in bounded_paths), (
        f"a value stored in the capture was read at the wider sysfs bound: {seen}"
    )
    assert wide_paths <= seen.keys(), f"an unstored path was never read at all: {wide_paths - seen.keys()}"
    assert all(seen[path] == linux_reader.MAX_SYSFS_BYTES for path in wide_paths), (
        f"a value that is never stored moved onto the narrower bound: {seen}"
    )


#: PowerShell 5.1's `>` (`Out-File`) writes UTF-16LE with a BOM; its
#: `-Encoding utf8` writes UTF-8 with one. PS 5.1 is the default shell on
#: Windows 10 and 11, and the documented `ssh host lsdsk snapshot -o - >
#: capture.json` recipe goes through it. `str.encode` adds the BOM itself for
#: these three codec names, so no test here hand-assembles a byte mark.
_BOM_ENCODINGS = (
    pytest.param("utf-8-sig", id="utf-8 with a BOM"),
    pytest.param("utf-16", id="utf-16 with a BOM, PowerShell 5.1's `>`"),
    pytest.param("utf-32", id="utf-32 with a BOM"),
)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("codec", _BOM_ENCODINGS)
def test_a_capture_saved_through_a_bom_carrying_encoding_still_replays(tmp_path: Path, codec: str) -> None:
    """A capture written by PowerShell 5.1's `>` or `-Encoding utf8` still loads.

    Read as plain UTF-8 before this, the UTF-16/UTF-32 bytes failed to decode
    at all (``'utf-8' codec can't decode byte 0xff``), and the UTF-8 BOM
    decoded cleanly but survived as a leading U+FEFF character that
    ``json.loads`` then refused on its own terms (``Unexpected UTF-8 BOM``).
    Both read as exit 78 for a file that is perfectly good JSON once its own
    mark is honoured.
    """
    source = SNAPSHOT.read_text(encoding="utf-8")
    encoded = tmp_path / "capture.json"
    encoded.write_bytes(source.encode(codec))

    control = load(SNAPSHOT)
    replayed = load(encoded)

    assert replayed.hostname == control.hostname
    assert [c.address for c in replayed.controllers] == [c.address for c in control.controllers]
    assert [d.node for d in replayed.disks] == [d.node for d in control.disks]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("codec", _BOM_ENCODINGS)
def test_a_history_store_saved_through_a_bom_carrying_encoding_still_loads(tmp_path: Path, codec: str) -> None:
    """The counter history is read back through the same BOM-aware decode as a capture.

    The two files that reach this tool from outside it share one reader
    (``read_json_bounded``), so the fix belongs to both or it is one edit from
    being absent at one - the same rule this file already applies to the
    duplicate-key guard.
    """
    text = '{"schema": 1, "hostname": "box", "series": []}'
    store = tmp_path / "history.json"
    store.write_bytes(text.encode(codec))

    history = load_history(store, hostname="box")

    assert history.hostname == "box"
    assert history.series == ()


@pytest.mark.os_agnostic
def test_a_capture_with_no_bom_that_is_not_valid_utf8_is_still_refused(tmp_path: Path) -> None:
    """A file naming no recognised mark is still decoded as strict UTF-8, and still refused.

    The control for every arm above: a byte string that opens with none of the
    five marks this reader now checks, and is not valid UTF-8 either, so BOM
    detection must not have widened what a genuinely unreadable file gets
    away with.
    """
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"\x80\x81\x82not valid utf-8")

    with pytest.raises(ConfigurationError, match="Could not read the snapshot"):
        load(invalid)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("codec", _BOM_ENCODINGS)
def test_a_duplicate_key_is_still_refused_in_a_bom_carrying_capture(tmp_path: Path, codec: str) -> None:
    """BOM detection decodes the bytes; it must not bypass the repeated-key guard.

    The same crafted text `test_a_capture_that_names_one_pci_address_twice_is_refused`
    uses, saved through each BOM-carrying codec instead of plain UTF-8.
    """
    crafted = tmp_path / "duplicate.json"
    crafted.write_bytes(_capture_text(pci=f"{_CONTROLLER}, {_GRAPHICS}", block=_BLOCK).encode(codec))

    with pytest.raises(ConfigurationError) as refusal:
        load(crafted)

    assert "0000:03:00.0" in str(refusal.value), "the refusal does not name the key that was repeated"


@pytest.mark.os_agnostic
def test_the_size_bound_is_on_bytes_read_not_on_the_decoded_character_count(tmp_path: Path) -> None:
    """A multi-byte encoding is bounded by the BYTES it takes, which is the stricter reading.

    `read_json_bounded` bounds the file it is handed before it decodes
    anything, so the same character count costs more bytes in UTF-16 (2 bytes
    per character here, all in the Basic Multilingual Plane) than in UTF-8
    (1 byte per padding character), and hits :data:`MAX_INPUT_BYTES` at
    roughly half the character count. That is the conservative direction: a
    real capture or history file is orders of magnitude under either ceiling,
    and nothing here is asked to decode a file whose bytes were never read
    in the first place.
    """
    padding = "x" * (MAX_INPUT_BYTES // 2)
    over_the_byte_bound = tmp_path / "over.json"
    # Two bytes per padding character in UTF-16, plus a 2-byte BOM: past
    # MAX_INPUT_BYTES in bytes despite every character living in the BMP.
    over_the_byte_bound.write_bytes(f'{{"pad": "{padding}"}}'.encode("utf-16"))

    with pytest.raises(ConfigurationError, match="far larger than a snapshot ever is"):
        read_json_bounded(over_the_byte_bound, what="a snapshot")


@pytest.mark.os_agnostic
def test_a_bom_carrying_capture_replays_end_to_end(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The documented `ssh host lsdsk snapshot -o - > capture.json` recipe, driven through `--replay`.

    `CliRunner` cannot see a broken pipe (:mod:`tests.test_cli_exit_codes`
    covers that over a real subprocess), but it drives the real CLI, the real
    container and the real `--replay` flag end to end, which a direct call to
    `load` does not.
    """
    from lsdsk.adapters.cli import cli

    encoded = tmp_path / "capture.json"
    encoded.write_bytes(SNAPSHOT.read_text(encoding="utf-8").encode("utf-16"))

    control = cli_runner.invoke(cli, ["disks", "--replay", str(SNAPSHOT)], obj=production_factory, color=False)
    result = cli_runner.invoke(cli, ["disks", "--replay", str(encoded)], obj=production_factory, color=False)

    # Not exit code 0: this fixture carries findings, so `disks` leaves this
    # tool's actionable-finding code on the plain path too. The property under
    # test is that the BOM-carrying replay behaves EXACTLY like the plain one,
    # not that either exits clean.
    assert result.exit_code == control.exit_code, (
        f"a UTF-16 capture with a BOM answered {result.exit_code}, the plain one {control.exit_code}: {result.output}"
    )
    assert result.stdout == control.stdout, "the BOM-carrying replay rendered differently from the plain one"
