"""What a file reaching lsdsk from outside it is allowed to do to it.

A capture handed to ``--replay`` and the store at ``--history-file`` are both
validated against a Pydantic model, but only after the whole file is already in
memory. These tests hold the guard that runs first, and the bounds a capture
that passed it still has to respect once its contents reach a renderer.
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_args

import annotated_types
import pytest
from rich.console import Console

from lsdsk.adapters.history.store import load_history
from lsdsk.adapters.hw import capture as shared_capture
from lsdsk.adapters.hw.capture import CaptureEnvelope, CaptureModel
from lsdsk.adapters.hw.linux import capture as linux_capture
from lsdsk.adapters.hw.snapshot import load
from lsdsk.adapters.hw.windows import capture as windows_capture
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

    with pytest.raises(MissingFileError):
        read_text_bounded(in_the_way / "store.json", what="a history store")

    with pytest.raises(ConfigurationError) as refused:
        read_text_bounded(tmp_path, what="a history store")
    assert not isinstance(refused.value, MissingFileError), (
        "the control: a directory that is genuinely there was called absent, so the arm above asserts nothing"
    )


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

    with pytest.raises(ConfigurationError):
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

    with pytest.raises(ConfigurationError):
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
