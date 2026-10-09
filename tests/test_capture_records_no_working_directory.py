"""A Windows capture carries no working directory.

The reader once recorded ``os.getcwd()`` as ``cwd``. Nothing reads it, the privacy
notice for a capture never named it, and it put the account's profile path into
every capture shipped as a fixture.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from lsdsk.adapters.hw.windows import reader

_FIXTURES = Path(__file__).parent / "fixtures" / "hw"
_WINDOWS_CAPTURES = sorted(_FIXTURES.glob("windows-*.json"))


@pytest.mark.os_agnostic
def test_the_windows_captures_under_test_are_found() -> None:
    assert len(_WINDOWS_CAPTURES) >= 2


@pytest.mark.os_agnostic
@pytest.mark.parametrize("capture", _WINDOWS_CAPTURES, ids=lambda path: path.stem)
def test_a_committed_windows_capture_holds_no_cwd_key_and_no_profile_path(capture: Path) -> None:
    text = capture.read_text(encoding="utf-8")
    assert "cwd" not in json.loads(text)
    assert "C:\\\\Users\\\\srvadmin" not in text


@pytest.mark.os_agnostic
def test_the_windows_reader_does_not_write_a_cwd_key() -> None:
    source = Path(reader.__file__).read_text(encoding="utf-8")
    keys = {node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)}
    assert "cwd" not in keys
