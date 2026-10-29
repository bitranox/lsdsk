"""A deploy says which of the reader's values the files it just created now override.

``config-deploy --target user`` over an existing ``config.toml`` keeps that file, as it should,
and creates the ``config.d`` companions beside it. Those are read after ``config.toml`` and so
outrank it: a ``wwn_width = 40`` the reader wrote became the shipped 24 the moment the deploy
reported success, with nothing on the screen to say so. The deploy still runs - refusing it would
block the ordinary first deploy of anybody who wrote ``config.toml`` by hand - but each value it
overrides is named with the file that now decides it, and how to keep the reader's value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib_layered_config import Config

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.deploy import values_the_deploy_shadows
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest
    from lib_layered_config.domain.config import SourceInfo

#: What every shadowing warning begins with.
SHADOWED = "Warning: display.wwn_width=40 in "
#: The hint said once after them.
HINT = "Hint:"


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    get_config.cache_clear()
    try:
        code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _own_config(user_config_dir: Path, body: str) -> Path:
    user_config_dir.mkdir(parents=True, exist_ok=True)
    own = user_config_dir / "config.toml"
    own.write_text(body, encoding="utf-8", newline="\n")
    return own


def _shadowing(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith(SHADOWED)]


def test_a_value_a_new_companion_overrides_is_named_with_the_file_that_now_wins(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    own = _own_config(user_config_dir, "[display]\nwwn_width = 40\n")

    code, _out, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    winner = user_config_dir / "config.d" / "70-display.toml"
    assert winner.is_file(), "the premise: the deploy created the display companion"
    named = _shadowing(err)
    assert len(named) == 1, err
    assert str(own) in named[0], named
    assert str(winner) in named[0], named
    assert "24" in named[0], named
    hints = [line for line in err.splitlines() if line.startswith(HINT)]
    assert len(hints) == 1, err
    assert "--force" in hints[0], hints
    assert "config.d" in hints[0], hints
    _code, out, _err = _run(["config", "--format", "json"], capsys)
    assert '"wwn_width": 24' in out, "the premise: the companion really decides the value now"


def test_a_value_equal_to_the_shipped_one_is_not_named(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: a companion that gives the reader's own value overrides nothing anybody sees."""
    _own_config(user_config_dir, "[display]\nwwn_width = 24\n")

    code, _out, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert [line for line in err.splitlines() if "display.wwn_width" in line] == [], err
    assert HINT not in err, err


def test_a_later_deploy_that_creates_nothing_names_nothing(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Said by the deploy that created the companion, once; one that writes nothing changes nothing."""
    _own_config(user_config_dir, "[display]\nwwn_width = 40\n")
    first_code, _out, first_err = _run(["config-deploy", "--target", "user"], capsys)
    assert first_code == 0, first_err
    assert _shadowing(first_err), "the premise: the first deploy named it"

    code, _out, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert _shadowing(err) == [], err


def test_a_forced_deploy_that_replaces_the_file_names_no_value_of_it(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--force`` replaces ``config.toml`` itself, which is what it says it does: nothing is shadowed."""
    _own_config(user_config_dir, "[display]\nwwn_width = 40\n")

    code, _out, err = _run(["config-deploy", "--target", "user", "--force"], capsys)

    assert code == 0, err
    assert _shadowing(err) == [], err


def test_the_hinted_file_of_ones_own_keeps_the_value(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The remedy the hint names works: a config.d file sorting after the shipped ones wins."""
    _own_config(user_config_dir, "[display]\nwwn_width = 40\n")
    code, _out, err = _run(["config-deploy", "--target", "user"], capsys)
    assert code == 0, err
    assert "config.d/99-local.toml" in err, "the premise: the hint names this file"

    local = user_config_dir / "config.d" / "99-local.toml"
    local.write_text("[display]\nwwn_width = 40\n", encoding="utf-8", newline="\n")
    _code, out, _err = _run(["config", "--format", "json"], capsys)

    assert '"wwn_width": 40' in out, out


def _origin(layer: str, path: Path, dotted: str) -> SourceInfo:
    return {"layer": layer, "path": str(path), "key": dotted}


def _shadowed_dotted(tmp_path: Path, *, before_layer: str) -> list[str]:
    """What ``values_the_deploy_shadows`` names when a user companion replaces a value of `before_layer`."""
    kept = tmp_path / "before" / "config.toml"
    winner = tmp_path / "user" / "config.d" / "70-display.toml"
    dotted = "display.wwn_width"
    before = Config({"display": {"wwn_width": 40}}, {dotted: _origin(before_layer, kept, dotted)})
    after = Config({"display": {"wwn_width": 24}}, {dotted: _origin("user", winner, dotted)})
    return [entry.dotted for entry in values_the_deploy_shadows(before, after, written=[winner])]


def test_a_value_from_a_lower_layer_that_a_new_user_file_outranks_is_not_named(tmp_path: Path) -> None:
    """Outranking a lower layer is what a deploy to a higher one is for: only a SAME-layer value is shadowed.

    The control beside it gives the same pair of files one layer and requires the entry, so the
    empty answer is the layer rule speaking rather than a comparison that never matches.
    """
    assert _shadowed_dotted(tmp_path, before_layer="user") == ["display.wwn_width"], "the control: same layer"
    assert _shadowed_dotted(tmp_path, before_layer="app") == []


def test_the_overridden_values_are_named_in_key_order_whatever_order_the_files_give_them(tmp_path: Path) -> None:
    kept = tmp_path / "config.toml"
    winner = tmp_path / "config.d" / "70-display.toml"
    # Merged in the order the files gave them, which is not key order: the last key first.
    keys = ("zeta", "alpha", "mid")
    before = Config(
        {"display": dict.fromkeys(keys, 40)},
        {f"display.{key}": _origin("user", kept, f"display.{key}") for key in keys},
    )
    after = Config(
        {"display": dict.fromkeys(keys, 24)},
        {f"display.{key}": _origin("user", winner, f"display.{key}") for key in keys},
    )

    named = [entry.dotted for entry in values_the_deploy_shadows(before, after, written=[winner])]

    assert named == ["display.alpha", "display.mid", "display.zeta"]
