"""Configuration too deeply nested for JSON output is refused by name, never crashed on.

pydantic-core writes every JSON envelope, and it stops at a nesting depth that
depends on the platform: measured with pydantic-core 2.46.5, 98 levels on Windows
and 254 on Linux, failing with "Circular reference detected (depth exceeded)".
lib_layered_config accepts a configuration nested 100 levels, and so did a
``--set``, so on Windows ``config --format json`` ended with exit 1 and a
traceback-free crash on a configuration both had accepted, while every Linux and
macOS run printed it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pydantic_core
import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.envelope import MappingResult, PayloadTooDeepError, emit_action
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.overrides import MAX_OVERRIDE_DEPTH
from lsdsk.domain.enums import ActionCommand

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from click.testing import CliRunner

#: The tightest JSON nesting ceiling measured across the platforms CI runs.
#:
#: pydantic-core 2.46.5 on Windows; Linux and macOS go to 254. A ``--set`` the tool
#: accepts must print within this on every platform, so it is asserted here on
#: whichever platform runs the suite rather than only on the one that has it.
TIGHTEST_JSON_DEPTH = 98

#: How deep lib_layered_config lets a configuration nest, section and keys included.
LIBRARY_DEPTH = 100


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> Iterator[None]:
    """Drop the cached configuration on both sides, so one test's file never reaches the next."""
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def _nested(depth: int) -> dict[str, Any]:
    """A mapping `depth` levels deep with a scalar at the bottom."""
    value: Any = 1
    for _ in range(depth):
        value = {"a": value}
    return value


def _children(item: object) -> list[object]:
    """What one parsed JSON value holds directly: an object's values, an array's items, or nothing."""
    if isinstance(item, dict):
        return list(cast("dict[str, object]", item).values())
    if isinstance(item, list):
        return list(cast("list[object]", item))
    return []


def _json_depth(value: object) -> int:
    """How many levels of objects and arrays `value` holds, a scalar being none."""
    depth = 0
    level: list[object] = [value]
    while any(isinstance(item, dict | list) for item in level):
        depth += 1
        level = [child for item in level for child in _children(item)]
    return depth


def _toml_inline(depth: int) -> str:
    """The TOML spelling of :func:`_nested`, as inline tables."""
    return "{a = " * depth + "1" + "}" * depth


@pytest.mark.os_agnostic
def test_a_payload_too_deep_to_write_is_refused_by_name_and_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """300 levels is past every platform's ceiling, so the refusal is reached everywhere."""
    payload = MappingResult.model_validate({"bogus": _nested(300)})

    with pytest.raises(PayloadTooDeepError, match="nests deeper"):
        emit_action(ActionCommand.CONFIG, payload)

    assert capsys.readouterr().out == "", "a refused envelope still wrote part of itself"


@pytest.mark.os_agnostic
def test_the_deepest_set_value_accepted_prints_within_the_tightest_platform_ceiling(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """``bogus.key`` is two levels, so the value may hold the rest of the limit and no more."""
    deepest = json.dumps(_nested(MAX_OVERRIDE_DEPTH - 2))
    result = cli_runner.invoke(
        cli, ["--set", f"bogus.key={deepest}", "config", "--format", "json"], obj=production_factory
    )

    assert result.exit_code == 0, f"left {result.exit_code}: {result.stderr[-300:]}"
    printed = _json_depth(json.loads(result.stdout))
    assert printed <= TIGHTEST_JSON_DEPTH, f"the deepest accepted --set prints {printed} levels deep"


@pytest.mark.os_agnostic
def test_a_configuration_at_the_librarys_limit_prints_or_is_refused_by_name(
    cli_runner: CliRunner, production_factory: Callable[[], Any], user_config_dir: Path
) -> None:
    """The arm is predicted from this platform's own serializer, so each runner proves the one it takes.

    The prediction serializes the same document the envelope carries; on Windows
    it fails and the refusal arm runs, on Linux and macOS it prints.
    """
    value_depth = LIBRARY_DEPTH - 2
    user_config_dir.mkdir(parents=True, exist_ok=True)
    (user_config_dir / "config.toml").write_text(f"[bogus]\nkey = {_toml_inline(value_depth)}\n", encoding="utf-8")
    document: dict[str, object] = {
        "ok": True,
        "command": "config",
        "data": {"bogus": {"key": _nested(value_depth)}},
        "skipped": [],
    }
    try:
        pydantic_core.to_json(document)
        printable = True
    except pydantic_core.PydanticSerializationError:
        printable = False

    result = cli_runner.invoke(cli, ["config", "--format", "json"], obj=production_factory)

    if printable:
        assert result.exit_code == 0, f"left {result.exit_code}: {result.stderr[-300:]}"
        assert json.loads(result.stdout)["data"]["bogus"]["key"] == _nested(value_depth)
    else:
        assert result.exit_code == ExitCode.CONFIG_ERROR, f"left {result.exit_code}: {result.stderr[-300:]}"
        said = " ".join(result.stderr.split())
        assert "nests deeper" in said, said
        assert "--format human" in said, f"the refusal does not say what still works: {said}"
        assert json.loads(result.stdout)["ok"] is False, "the refusal is not a JSON envelope"
