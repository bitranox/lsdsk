"""A command-line value that is not valid UTF-8 must not stop the JSON envelope being written.

A path or a name arrives from the operating system as bytes Python decodes to a lone
surrogate, and a refusal quotes it. The JSON writer cannot encode a surrogate, so
every refusal that quoted one left exit 70, an empty stdout and a pydantic
traceback: the caller that asked for JSON got neither a document nor the reason.
The envelope sanitises the text it carries, so this holds for every command.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Iterator

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
BAD_NAME = "bad\udcffname.json"


@pytest.fixture(autouse=True)
def no_cached_configuration() -> Iterator[None]:
    """Keep the loader's per-process cache from carrying a value across tests."""
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    exit_code = cli_mod.main(argv, services_factory=build_production)
    captured = capsys.readouterr()
    assert exit_code != 70, captured.err
    return exit_code, captured.out


@pytest.mark.os_posix
def test_a_history_file_with_undecodable_bytes_in_its_name_still_yields_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal quotes the history path, and the path holds a byte no UTF-8 text can."""
    store = Path(os.fsdecode(os.fsencode(tmp_path) + b"/" + os.fsencode(BAD_NAME)))
    store.write_bytes(b"not a history store")

    exit_code, out = _run(
        ["--history-file", str(store), "--no-record", "--replay", str(CAPTURE), "disks", "--format", "json"], capsys
    )

    assert exit_code != 70
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert "\\udcff" in " ".join(envelope["skipped"])


@pytest.mark.os_agnostic
def test_a_profile_with_undecodable_bytes_still_yields_a_json_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    """``--profile`` is refused with its own text quoted, and the envelope carries it."""
    exit_code, out = _run(["config", "--profile", "\udcff\udcfe", "--format", "json"], capsys)

    assert exit_code != 70
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["error"]["type"]


@pytest.mark.os_agnostic
def test_a_section_with_undecodable_bytes_still_yields_a_json_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    """``--section`` names nothing, the refusal quotes it, and the envelope still parses."""
    exit_code, out = _run(["config", "--section", "\udcff", "--format", "json"], capsys)

    assert exit_code != 70
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert "\\udcff" in envelope["error"]["message"]


@pytest.mark.os_agnostic
def test_ordinary_text_is_carried_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    """The control: an accented section name survives, so the sanitiser touches only what cannot be encoded."""
    exit_code, out = _run(["config", "--section", "grüße", "--format", "json"], capsys)

    assert exit_code != 70
    assert "grüße" in json.loads(out)["error"]["message"]
