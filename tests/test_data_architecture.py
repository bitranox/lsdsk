"""Tests for the typed boundaries: the replay envelope in, the JSON envelope out.

Both are contracts with something outside this program, so both are pinned here
by their exact wire form rather than by the types that happen to produce it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.capture import CaptureEnvelope
from lsdsk.adapters.hw.snapshot import load
from lsdsk.domain.enums import CliCommand, Platform
from lsdsk.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("member", "wire"),
    [
        (Platform.LINUX, "linux"),
        (Platform.WINDOWS, "win32"),
        (CliCommand.TOPOLOGY, "topology"),
        (CliCommand.SLOTS, "slots"),
    ],
)
def test_enum_members_keep_their_wire_form(member: str, wire: str) -> None:
    """Verify interpolation yields the value, not the member name.

    These cross a boundary as strings: a capture records its platform and a
    consumer reads the envelope's command. A member that formats as
    ``Platform.LINUX`` would break both, and would do it on some interpreter
    versions and not others.
    """
    assert f"{member}" == wire
    assert member == wire


@pytest.mark.os_agnostic
def test_a_malformed_snapshot_is_rejected_at_the_boundary(tmp_path: Path) -> None:
    """Verify replay input is validated where it enters, not deep in a builder.

    ``--replay`` takes a file from any machine. Without validation here a bad
    file surfaces as a KeyError inside a platform builder, which reads as a bug
    in lsdsk rather than as a bad input file.
    """
    unknown_platform = tmp_path / "unknown.json"
    unknown_platform.write_text(json.dumps({"schema": 1, "platform": "solaris"}), encoding="utf-8")
    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text(json.dumps({"schema": 1, "platform": "linux", "hostname": []}), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not a snapshot lsdsk understands"):
        load(unknown_platform)
    with pytest.raises(ConfigurationError, match="not a snapshot lsdsk understands"):
        load(wrong_type)


_LINUX_HEADER: dict[str, object] = {"schema": 2, "platform": "linux", "hostname": "h", "kernel": "6.1.0", "pci": {}}
_WINDOWS_HEADER: dict[str, object] = {"schema": 2, "platform": "win32", "hostname": "h", "kernel": "10.0", "pci": {}}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "capture",
    [
        pytest.param({**_LINUX_HEADER, "classes": "x"}, id="linux-classes-not-a-mapping"),
        pytest.param({**_LINUX_HEADER, "classes": {"scsi_host": "x"}}, id="linux-class-not-a-mapping"),
        pytest.param({**_LINUX_HEADER, "block": "x"}, id="linux-block-not-a-mapping"),
        pytest.param({**_LINUX_HEADER, "block": {"sda": "x"}}, id="linux-block-entry-not-a-mapping"),
        pytest.param({**_LINUX_HEADER, "pci": {"0000:00:1f.2": "x"}}, id="linux-pci-entry-not-a-mapping"),
        pytest.param({**_LINUX_HEADER, "pci_names": "x"}, id="linux-pci-names-not-a-mapping"),
        pytest.param({**_LINUX_HEADER, "environment": "x"}, id="environment-is-text"),
        pytest.param({**_LINUX_HEADER, "environment": ["x"]}, id="environment-is-a-list"),
        pytest.param({**_LINUX_HEADER, "environment": {"dmi_board_name": 42}}, id="board-name-is-a-number"),
        pytest.param({**_WINDOWS_HEADER, "disks": "x"}, id="windows-disks-not-a-mapping"),
        pytest.param({**_WINDOWS_HEADER, "disks": {"PhysicalDrive0": "x"}}, id="windows-disk-entry-not-a-mapping"),
        pytest.param({**_WINDOWS_HEADER, "pci": {"PCI\\VEN_8086": "x"}}, id="windows-pci-entry-not-a-mapping"),
    ],
)
def test_a_wrong_shaped_section_is_refused_as_a_bad_file(tmp_path: Path, capture: dict[str, object]) -> None:
    """Verify every section a builder reads is checked where the file enters.

    The outer keys are not the whole contract: each section a builder reads has
    a shape too. Checked only at the outer keys, a wrong-shaped section reached a
    builder, which either died there with an AttributeError - a traceback under
    the wrong exit code, reading as a bug in lsdsk rather than a bad file - or
    dropped the value without a word.
    """
    snapshot = tmp_path / "malformed.json"
    snapshot.write_text(json.dumps(capture), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not a snapshot lsdsk understands"):
        load(snapshot)


@pytest.mark.os_agnostic
def test_a_wrong_shaped_section_exits_with_the_configuration_code(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify the refusal reaches the command line as a bad input, not a crash."""
    from lsdsk.adapters.cli import cli
    from lsdsk.adapters.cli.exit_codes import ExitCode

    snapshot = tmp_path / "malformed.json"
    snapshot.write_text(json.dumps({**_LINUX_HEADER, "block": {"sda": "x"}}), encoding="utf-8")

    result = cli_runner.invoke(cli, ["topology", "--replay", str(snapshot)], obj=production_factory)

    assert result.exit_code == ExitCode.CONFIG_ERROR


@pytest.mark.os_agnostic
def test_a_real_snapshot_still_loads() -> None:
    """Verify the new validation does not reject what the reader writes."""
    assert load(FIXTURE).hostname == "linux-nvme-board"


@pytest.mark.os_agnostic
def test_the_envelope_accepts_keys_it_does_not_model() -> None:
    """Verify a key no model names passes through rather than failing the snapshot.

    A newer reader may record more than this version models. Refusing its
    snapshot for that would break replay across versions for no gain, so an
    unmodelled key is ignored while a modelled one must still hold its type.
    """
    envelope = CaptureEnvelope.model_validate(
        {
            "schema": 1,
            "platform": "linux",
            "hostname": "example",
            "kernel": "6.1.0",
            "pci": {},
            "something_new": 42,
        }
    )

    assert envelope.platform is Platform.LINUX
    assert envelope.schema_version == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "command",
    # Taken from the enum rather than listed by hand: a hand-written list means a
    # new command is simply not covered, and nothing says so.
    [member.value for member in CliCommand],
)
def test_each_command_names_itself_in_the_envelope(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    command: str,
) -> None:
    """Verify the envelope says which command produced it.

    The builder is shared, so a literal in it made every command claim to be
    ``scan``: a consumer scripting against the output could not tell one from
    another.
    """
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(cli, [command, "--replay", str(FIXTURE), "--format", "json"], obj=production_factory)
    payload = json.loads(result.stdout)

    assert payload["command"] == command
    assert payload["ok"] is True


@pytest.mark.os_agnostic
def test_the_envelope_serialises_enums_as_their_values(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify no enum reaches the wire as its repr.

    A consumer matches on ``"warning"``, never on ``"Severity.WARNING"``, and the
    difference is invisible until something downstream fails to match.
    """
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(cli, ["topology", "--replay", str(FIXTURE), "--format", "json"], obj=production_factory)
    text = result.stdout

    for enum_name in ("ControllerKind.", "Severity.", "BusType.", "DiskKind.", "Environment.", "Platform."):
        assert enum_name not in text, f"{enum_name} leaked into the envelope as a repr"
    payload = json.loads(text)
    assert payload["data"]["controllers"][0]["kind"] == "ahci"


@pytest.mark.os_agnostic
def test_every_tui_page_has_a_command_of_the_same_name() -> None:
    """Verify the two surfaces stay one vocabulary.

    A page called one thing and the command for it called another makes the
    reader translate between them, and nothing but this check stops the two
    drifting: each is registered in its own place.
    """
    from lsdsk.adapters.cli import cli
    from lsdsk.adapters.tui.app import LsdskApp

    commands = set(cli.commands)

    missing = [page for page in LsdskApp.PAGES if page not in commands]

    assert not missing, f"TUI pages with no command of the same name: {missing}"


@pytest.mark.os_agnostic
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows chmod only toggles read-only")
def test_a_snapshot_is_written_owner_only(tmp_path: Path) -> None:
    """Verify a capture is not left world-readable.

    It names the machine, its kernel and every drive's serial number, and the run
    that produces the most complete one is a privileged run. At the ambient umask
    that file lands group- and world-readable.
    """
    from lsdsk.adapters.hw.snapshot import SCHEMA_VERSION, SNAPSHOT_FILE_MODE, save

    target = tmp_path / "capture.json"
    # A real capture's shape, because save() parses a reading through the models
    # load() reads it back with before it writes: a stub shaped like no capture
    # is refused there, which is the point of that check rather than a problem
    # with it.
    save({"schema": SCHEMA_VERSION, "platform": "linux", "hostname": "example", "kernel": "6.1.0", "pci": {}}, target)

    assert target.stat().st_mode & 0o777 == SNAPSHOT_FILE_MODE
    assert not target.stat().st_mode & 0o077, "no group or world access"


@pytest.mark.os_agnostic
def test_examples_in_click_docstrings_name_commands_that_exist() -> None:
    """Verify an unrunnable example still tells the truth.

    A doctest inside a Click-decorated docstring is never collected: the
    docstring belongs to the Command object, not to a function, so pytest's
    scanner never reaches it. Six of them exist and cannot fail, which is exactly
    how one came to invoke a command that had been deleted while the suite stayed
    green. Nothing else checks them, so this does.
    """
    import ast
    import pathlib
    import re

    from lsdsk.adapters.cli import cli

    known = set(cli.commands)
    offenders: list[str] = []
    for path in pathlib.Path("src/lsdsk").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            doc = ast.get_docstring(node) or ""
            invoked = re.findall(r'runner\.invoke\(\s*cli\s*,\s*\[\s*"([a-z-]+)"', doc)
            offenders.extend(f"{path}::{node.name} invokes {name!r}" for name in invoked if name not in known)

    assert not offenders, f"docstring examples naming commands that do not exist: {offenders}"


@pytest.mark.os_agnostic
def test_an_action_payload_reaches_the_wire_with_its_own_fields() -> None:
    """Verify the envelope's payload is serialised by what it IS, not by its base.

    Pydantic serialises a field by its DECLARED type, so a field annotated as the
    bare result base - which declares no fields of its own - emits an empty
    object for every payload put in it. Measured on pydantic 2.13.5: the same
    envelope with a plain base annotation dumps {"data":{}}. The type checker
    passes either way and so does a test that asserts only the outer keys, so
    this asserts the payload's own field is on the wire.
    """
    from lsdsk.adapters.cli.envelope import ActionEnvelope, ActionResult
    from lsdsk.domain.enums import ActionCommand

    class Wrote(ActionResult):
        path: str

    envelope = ActionEnvelope(ok=True, command=ActionCommand.SNAPSHOT, data=Wrote(path="/tmp/capture.json"))

    assert json.loads(envelope.model_dump_json(by_alias=True))["data"] == {"path": "/tmp/capture.json"}


@pytest.mark.os_agnostic
def test_an_action_result_refuses_a_field_it_does_not_declare() -> None:
    """Verify a misspelled payload field is refused here, not silently dropped.

    The envelope is the contract another program reads, and pydantic ignores an
    unknown key by default: a payload built with a typo would emit without it and
    nothing would say so.
    """
    import pydantic

    from lsdsk.adapters.cli.commands.history import RecordResult

    with pytest.raises(pydantic.ValidationError):
        RecordResult.model_validate({"recorded": True, "store": "/tmp/h.json", "drives": 2, "drivs": 3})


@pytest.mark.os_agnostic
def test_every_action_result_can_read_the_wire_form_it_writes() -> None:
    """Verify each result model validates its OWN emitted payload.

    A model that renames a field for the wire writes a key it cannot read unless
    the alias goes both ways, and nothing else notices: the emit path only ever
    dumps, so a serialisation-only alias looks correct until something parses the
    output back. ``SnapshotResult`` was exactly that - it emitted ``{"schema": 1}``
    and its own validation refused it, because ``schema`` is a field it does not
    declare and the base forbids those.

    The subclasses are ENUMERATED rather than listed, and the command modules are
    walked rather than imported by name, so a result model added later is covered
    without anyone remembering to add it here. That is the point: the defect this
    guards is silent, so a list somebody maintains would not hold.

    A field type this cannot build fails the test by name rather than being
    skipped, because a skipped arm is an unguarded arm.
    """
    import importlib
    import pkgutil
    import typing

    import lsdsk.adapters.cli.commands as commands_package
    from lsdsk.adapters.cli.envelope import ActionResult

    for module in pkgutil.walk_packages(commands_package.__path__, f"{commands_package.__name__}."):
        importlib.import_module(module.name)

    def a_value_for(annotation: object) -> object:
        """Build one value of the declared type, or say which type stopped us."""
        origin = typing.get_origin(annotation)
        if origin is list:
            return []
        if origin is not None and type(None) in typing.get_args(annotation):
            return None
        simple: dict[object, object] = {str: "x", int: 1, bool: True, float: 1.0}
        if annotation not in simple:
            raise AssertionError(f"this guard cannot build a {annotation!r}; teach it that type")
        return simple[annotation]

    unreadable: list[str] = []
    for result in ActionResult.__subclasses__():
        payload = result(**{name: a_value_for(field.annotation) for name, field in result.model_fields.items()})
        wire = payload.model_dump_json(by_alias=True)
        try:
            assert result.model_validate_json(wire) == payload
        except Exception as exc:
            unreadable.append(f"{result.__name__} emitted {wire} and could not read it back: {type(exc).__name__}")

    assert not unreadable, "result models that cannot parse their own wire form: " + "; ".join(unreadable)


#: Where a module's public surface is declared, and where it is consumed.
_SOURCE = Path(__file__).resolve().parent.parent / "src" / "lsdsk"
_CONSUMERS = (_SOURCE, Path(__file__).resolve().parent, _SOURCE.parent.parent / "scripts")


def _exported_and_public(path: Path) -> tuple[set[str] | None, set[str]]:
    """What a module lists in `__all__`, and every public name it declares."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    exported: set[str] | None = None
    public: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(getattr(target, "id", "") == "__all__" for target in node.targets)
            and isinstance(node.value, ast.List)
        ):
            exported = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_"):
            public.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_") and target.id != "__all__":
                    public.add(target.id)
        elif (
            isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and not node.target.id.startswith("_")
        ):
            public.add(node.target.id)
    return exported, public


def _names_reached_from_elsewhere() -> dict[str, set[Path]]:
    """Every name any file imports or reaches through a module, by the file that does."""
    import ast
    from collections import defaultdict

    reached: dict[str, set[Path]] = defaultdict(set)
    for root in _CONSUMERS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        reached[alias.name].add(path)
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    reached[node.attr].add(path)
    return reached


@pytest.mark.os_agnostic
def test_a_name_other_modules_reach_for_is_one_its_own_module_exports() -> None:
    """`__all__` states the public surface, so it cannot omit what is consumed.

    Measured before this: 66 names across 17 modules were imported or reached
    through their module elsewhere and left out of its own `__all__` -
    `theme.NOT_READ` and `theme.LEGACY`, every one of `layout`'s tree glyphs,
    `models.pci_class_name`, `diagnostics.refine` and `ports.ReadHistory`
    among them, three of which CLAUDE.md names as the vocabulary of a
    documented law. A list that omits what is consumed is not a smaller
    contract, it is a wrong one: `from lsdsk.application import GetConfig`
    worked and `ReadHistory` did not, for no reason anybody chose.

    The rule is deliberately not "every public name is exported": a module is
    free to keep a name to itself. It is CONSUMPTION that makes a name part of
    the surface, so the check asks what other files actually reach for.
    """
    unexported: dict[str, list[str]] = {}
    reached = _names_reached_from_elsewhere()
    for path in sorted(_SOURCE.rglob("*.py")):
        exported, public = _exported_and_public(path)
        if exported is None:
            continue
        missing = sorted(name for name in public - exported if any(other != path for other in reached.get(name, set())))
        if missing:
            unexported[str(path.relative_to(_SOURCE))] = missing

    assert reached, "no name was found to be reached at all, so this asserted nothing"
    assert not unexported, f"names consumed elsewhere and missing from their own __all__: {unexported}"


@pytest.mark.os_agnostic
def test_a_refused_capture_is_explained_in_this_tool_s_own_words(tmp_path: Path) -> None:
    """Pydantic's report is a developer's document, not a refusal for a caller.

    Interpolating `str(ValidationError)` put `tagged-union[LinuxCapture,
    WindowsCapture]` in front of a reader, a pinned pydantic version in a URL
    they were invited to follow, and a truncated slice of their own file -
    four such blocks for an empty object. It does name the real cause, which is
    why the fields and the reasons are kept; what goes is the framework's
    packaging, which every other refusal in this tool does without.
    """
    crafted = tmp_path / "empty.json"
    crafted.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigurationError) as refused:
        load(crafted)

    said = str(refused.value)
    assert "errors.pydantic.dev" not in said, f"a pydantic URL reached the caller:\n{said}"
    assert "tagged-union" not in said, f"a pydantic internal type name reached the caller:\n{said}"
    assert "input_value" not in said, f"pydantic's own field names reached the caller:\n{said}"
    # And it still says WHAT is wrong, field by field, or the refusal is useless.
    assert "schema" in said and "hostname" in said, f"the refusal names no field:\n{said}"
    assert "required" in said.lower(), f"the refusal gives no reason:\n{said}"
