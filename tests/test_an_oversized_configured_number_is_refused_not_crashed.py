"""A configured number too long to print as text is refused, never a crash.

Python refuses to turn an int of more than 4300 decimal digits into text, and a YAML drop-in in
``config.d`` can carry one: PyYAML reads a hex literal with ``int(text, 16)``, which has no such
limit. Two layers answer it. lib_layered_config refuses any integer outside -2**63..2**64-1 when
it loads the file, so a drop-in carrying one is a configuration error like a file that is not
valid TOML: exit 78, naming the file and the key. Behind that, a value this tool cannot use is
named by its size (``<int, 16000 bits>``) rather than spelt with ``repr``, which raised and turned
the warning itself into exit 70; the section-reader tests below hold that layer on its own, since
a loaded file no longer reaches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from lib_layered_config import Config

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.history import read_history_settings
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.values import rendered
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from pathlib import Path

#: 16000 bits, about 4817 decimal digits: past the interpreter's 4300-digit text limit.
OVERSIZED = (1 << 16000) - 1


def test_an_oversized_history_path_is_reported_as_refused() -> None:
    """Through the real section reader, so both the path test and the warning text are reached."""
    reading = read_history_settings(Config({"history": {"path": OVERSIZED}}, {}))

    assert [refused.dotted for refused in reading.rejected] == ["history.path"]
    assert reading.rejected[0].raw == "<int, 16000 bits>"


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        pytest.param(OVERSIZED, "<int, 16000 bits>", id="int"),
        pytest.param(-OVERSIZED, "<int, 16000 bits>", id="negative-int"),
        pytest.param([OVERSIZED], "<list holding a number too long to show>", id="inside-a-list"),
        pytest.param(4711, "4711", id="ordinary-int"),
    ],
)
def test_a_value_too_long_to_print_is_named_by_its_size(value: object, shown: str) -> None:
    assert rendered(value) == shown


def test_an_oversized_number_in_a_yaml_drop_in_is_refused_as_a_configuration_error(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The route a reader can actually take, where PyYAML is installed for lib_layered_config.

    Refused at load, the same way as a drop-in that does not parse: ``78`` (``EX_CONFIG``) with
    the file and the key named, and none of the library's class name or a traceback.
    """
    pytest.importorskip("yaml")
    drop_ins = user_config_dir / "config.d"
    drop_ins.mkdir(parents=True)
    (drop_ins / "99-oversized.yaml").write_text(f"history:\n  path: 0x{'F' * 4000}\n", encoding="utf-8", newline="\n")
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(["info"], services_factory=build_production)
    finally:
        get_config.cache_clear()

    said = " ".join(capsys.readouterr().err.split())
    assert exit_code == ExitCode.CONFIG_ERROR, said[-400:]
    assert said.startswith("Error:"), said[-400:]
    assert "99-oversized.yaml" in said, said[-400:]
    assert "history.path (int, 16000 bits)" in said, said[-400:]
    assert "LayerLoadError" not in said, said[-400:]
    assert "Traceback" not in said, said[-400:]
