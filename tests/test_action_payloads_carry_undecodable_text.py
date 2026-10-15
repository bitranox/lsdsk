r"""Text that is not valid UTF-8 must not stop an acting command's JSON being written.

A path or a value arrives from the operating system as bytes Python decodes to a
lone surrogate, and no JSON encoder can write one. The error envelope already
escapes the text it quotes; the acting commands' RESULT payloads did not, so a
history store, a capture path, an examples directory, a deployed file or a
configuration value holding such a byte ended the run with exit 70, an empty
stdout and a pydantic traceback after the work had been done.

The escape is applied where the envelope is exported, not field by field, so a
field a later command adds cannot forget it.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from undecodable_names import NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.envelope import ActionResult, MappingResult, emit_action
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production
from lsdsk.domain.enums import ActionCommand

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

CAPTURE = str(Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json")
BAD_NAME = "bad\udcffname"
ESCAPED = "bad\\udcffname"


@pytest.fixture(autouse=True)
def no_cached_configuration() -> Iterator[None]:
    """Keep the loader's per-process cache from carrying a value across tests."""
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _make(path: Path, *, directory: bool) -> None:
    try:
        if directory:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.write_bytes(b"x")
    except OSError as error:
        # macOS refuses a name that is not valid UTF-8 with EILSEQ, so nothing can carry one there.
        if error.errno != errno.EILSEQ:
            raise
        pytest.skip("this filesystem refuses a file name that is not valid UTF-8 (EILSEQ)")


def _strings(value: object) -> str:
    """Every string leaf of a parsed document, joined, so a test reads the text and not its JSON spelling."""
    if isinstance(value, dict):
        return " ".join(_strings(item) for item in cast("dict[str, object]", value).values())
    if isinstance(value, list):
        return " ".join(_strings(item) for item in cast("list[object]", value))
    return value if isinstance(value, str) else ""


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = cli_mod.main(argv, services_factory=build_production)
    captured = capsys.readouterr()
    assert code != 70, captured.err
    assert captured.out, f"nothing on stdout; stderr said {captured.err!r}"
    return code, json.loads(captured.out)


@NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM
@pytest.mark.os_posix
def test_record_names_a_store_with_undecodable_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``RecordResult.store`` is the history file's path, and the store was written before the envelope."""
    _make(tmp_path / BAD_NAME, directory=True)
    store = tmp_path / BAD_NAME / "history.json"
    argv = ["--history-file", str(store), "--replay", CAPTURE, "record", "--format", "json"]

    _, envelope = _run(argv, capsys)

    assert envelope["command"] == "record"
    assert ESCAPED in _strings(envelope)


@NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM
@pytest.mark.os_posix
def test_snapshot_names_an_output_with_undecodable_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``SnapshotResult.path`` echoes the ``-o`` value."""
    _make(tmp_path / BAD_NAME, directory=True)
    target = tmp_path / BAD_NAME / "capture.json"

    code, envelope = _run(["snapshot", "-o", str(target), "--format", "json"], capsys)

    assert code == 0
    assert envelope["ok"] is True
    assert ESCAPED in _strings(envelope)


@NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM
@pytest.mark.os_posix
def test_generate_examples_names_a_destination_with_undecodable_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``GenerateExamplesResult.destination`` and ``generated`` both quote the directory."""
    destination = tmp_path / BAD_NAME
    _make(destination, directory=True)

    code, envelope = _run(["config-generate-examples", "--destination", str(destination), "--format", "json"], capsys)

    assert code == 0
    assert ESCAPED in _strings(envelope)


@NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM
@pytest.mark.os_posix
def test_deploy_names_a_config_directory_with_undecodable_bytes(
    user_config_dir_under: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``DeployResult.deployed`` lists the files, whose directory carries the byte."""
    _make(user_config_dir_under(BAD_NAME), directory=True)

    code, envelope = _run(["config-deploy", "--target", "user", "--format", "json"], capsys)

    assert code == 0
    assert ESCAPED in _strings(envelope)


def test_config_carries_a_set_value_with_undecodable_bytes(capsys: pytest.CaptureFixture[str]) -> None:
    """The merged mapping is data: a ``--set`` value reaches it verbatim."""
    code, envelope = _run(["--set", f"lib_log_rich.service={BAD_NAME}", "config", "--format", "json"], capsys)

    assert code == 0
    assert ESCAPED in _strings(envelope)


class _Unannotated(ActionResult):
    """A payload written the way a new command would write it: plain ``str`` fields, nothing marked."""

    path: str
    paths: list[str]


def test_a_field_nobody_marked_is_still_escaped(capsys: pytest.CaptureFixture[str]) -> None:
    """The structural half: no field type opts in, so a field added later is covered too."""
    emit_action(
        ActionCommand.SNAPSHOT,
        _Unannotated(path=BAD_NAME, paths=[BAD_NAME]),
    )

    data = json.loads(capsys.readouterr().out)["data"]
    assert data == {"path": ESCAPED, "paths": [ESCAPED]}


def test_a_mapping_payload_is_escaped_at_every_depth(capsys: pytest.CaptureFixture[str]) -> None:
    """``MappingResult`` has no declared fields at all, so the escape cannot live on them.

    A key is not part of this: pydantic refuses a non-UTF-8 extra key at validation, so none reaches the export.
    """
    emit_action(
        ActionCommand.CONFIG, MappingResult.model_validate({"outer": {"inner": [BAD_NAME], "pair": (BAD_NAME,)}})
    )

    assert json.loads(capsys.readouterr().out)["data"] == {"outer": {"inner": [ESCAPED], "pair": [ESCAPED]}}


def test_ordinary_text_in_a_payload_is_carried_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    """The control: only what no JSON can hold is rewritten."""
    emit_action(ActionCommand.SNAPSHOT, _Unannotated(path="gr\u00fc\u00dfe", paths=["a b"]))

    assert json.loads(capsys.readouterr().out)["data"]["path"] == "grüße"
