"""A configuration key this tool does not read must not pass in silence.

Measured before the fix, on both surfaces that can carry one:

    --set thresholds.wear_warning_percent=1   -> 16 findings
    --set thresholds.wear_warnning_percent=1  ->  5 findings, the shipped default
    the same typo in ~/.config/lsdsk/config.d -> 5 findings, the shipped default

One transposed letter and the override was inert, with exit 0 and nothing on any
stream. ``lsdsk config`` even printed the unknown key back, so the operator got
positive confirmation of a setting that decided nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result

#: A capture whose drives are healthy enough that a lowered wear threshold
#: visibly changes the verdict, which is what makes the control arm mean
#: something.
_CAPTURE = "linux-nvme-board.json"


def _findings_count(output: str) -> int:
    """How many findings an envelope reports."""
    import json

    payload: Any = json.loads(output)
    data: Any = payload["data"]
    findings: Any = data["findings"]
    return len(findings)


@pytest.mark.os_agnostic
def test_a_real_threshold_override_moves_the_verdict(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """The control for every arm below: the mechanism works when the key is right.

    Without this, a test asserting that a typo changes nothing would pass just
    as well against a build where ``--set`` had stopped working altogether.
    """
    from pathlib import Path as _Path

    capture = _Path(__file__).parent / "fixtures" / "hw" / _CAPTURE
    argv = ["--no-record", "--history-file", str(tmp_path / "h.json")]
    tail = ["findings", "--replay", str(capture), "--format", "json"]

    plain: Result = cli_runner.invoke(cli_mod.cli, [*argv, *tail], obj=production_factory)
    lowered: Result = cli_runner.invoke(
        cli_mod.cli, [*argv, "--set", "thresholds.wear_warning_percent=1", *tail], obj=production_factory
    )

    # stdout, never ``output``: the latter merges stderr in, and whether the
    # logging adapter has been configured by an earlier test decides whether a
    # ``configuration_merged`` line sits in front of the envelope. The tool's
    # real stdout is the envelope alone, which is the thing worth asserting on.
    assert _findings_count(lowered.stdout) > _findings_count(plain.stdout), (
        "lowering the wear threshold changed nothing, so this file's arms prove nothing"
    )


@pytest.mark.os_agnostic
def test_a_mistyped_key_in_an_owned_section_is_refused_rather_than_ignored(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """A typo in a section whose keys this tool owns cannot be anything but a mistake."""
    from pathlib import Path as _Path

    capture = _Path(__file__).parent / "fixtures" / "hw" / _CAPTURE
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--no-record",
            "--history-file",
            str(tmp_path / "h.json"),
            "--set",
            "thresholds.wear_warnning_percent=1",
            "findings",
            "--replay",
            str(capture),
            "--format",
            "json",
        ],
        obj=production_factory,
    )

    assert result.exit_code == 2, f"a mistyped threshold exited {result.exit_code}, so it was applied to nothing"
    assert "wear_warning_percent" in result.output, "the refusal did not name the key the reader meant"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "spec",
    [
        pytest.param("lib_log_rich.some_key_we_do_not_ship=1", id="a library's own section"),
        pytest.param("nosuchsection.key=1", id="a section nothing declares"),
    ],
)
def test_a_key_outside_an_owned_section_is_left_alone(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    spec: str,
) -> None:
    """Only the case that can be PROVEN a typo is refused.

    ``lib_log_rich`` and ``lib_layered_config`` accept keys this project ships no
    line for, and a top-level key may belong to a ``.env`` other tooling reads.
    Refusing there would fail a caller over somebody else's business.
    """
    from pathlib import Path as _Path

    capture = _Path(__file__).parent / "fixtures" / "hw" / _CAPTURE
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--no-record",
            "--history-file",
            str(tmp_path / "h.json"),
            "--set",
            spec,
            "findings",
            "--replay",
            str(capture),
            "--format",
            "json",
        ],
        obj=production_factory,
    )

    assert result.exit_code != 2, f"{spec} was refused, and nothing here can prove it is a mistake"


def _run_with_config(*, config_home: Path, history: Path, capture: Path) -> tuple[int, str, str]:
    """Run a real lsdsk process against a config directory of this test's own.

    Spawned rather than invoked in-process because the layered loader caches its
    answer and resolves the user layer from the environment at import time, so
    an in-process arm would either read the developer's own config or assert
    against a cache the test filled itself.

    Args:
        config_home: What to hand the run as ``XDG_CONFIG_HOME``.
        history: A counter store of this test's own.
        capture: The snapshot to replay.

    Returns:
        The exit code, stdout and stderr.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path as _Path

    process = subprocess.run(  # noqa: S603 - argv is built here, no shell
        [
            sys.executable,
            "-m",
            "lsdsk",
            "--no-record",
            "--history-file",
            str(history),
            "findings",
            "--replay",
            str(capture),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_Path(__file__).parent.parent),
        env={**os.environ, "TERM": "dumb", "XDG_CONFIG_HOME": str(config_home)},
        check=False,
        timeout=120,
    )
    return process.returncode, process.stdout, process.stderr


@pytest.mark.os_posix
@pytest.mark.parametrize(
    ("key", "warns"),
    [
        pytest.param("wear_warnning_percent", True, id="a typo warns"),
        pytest.param("wear_warning_percent", False, id="the real key is silent"),
    ],
)
def test_a_mistyped_key_in_a_config_file_is_reported_rather_than_ignored(
    tmp_path: Path,
    key: str,
    warns: bool,
) -> None:
    """A file is warned about, never refused.

    The asymmetry with ``--set`` is deliberate and is about who else writes the
    file. A ``--set`` is typed for one run and has exactly one consumer, so an
    unknown key there is always a mistake and costs nothing to refuse. A config
    file is a durable artifact in a namespace that libraries and later versions
    also write into, so refusing would make it brittle.

    The silent arm is the control: it proves the warning is keyed on the key
    being unknown rather than on a config file merely being present.
    """
    from pathlib import Path as _Path

    config_home = tmp_path / "config"
    (config_home / "lsdsk" / "config.d").mkdir(parents=True)
    (config_home / "lsdsk" / "config.d" / "90-probe.toml").write_text(f"[thresholds]\n{key} = 1\n", encoding="utf-8")
    capture = _Path(__file__).parent / "fixtures" / "hw" / _CAPTURE

    code, out, err = _run_with_config(config_home=config_home, history=tmp_path / "h.json", capture=capture)

    assert out, "the run produced no stdout, so neither arm asserted anything"
    if warns:
        assert key in err, f"a file carrying {key!r} said nothing about it on stderr"
        assert "wear_warning_percent" in err, "the warning did not name the key the reader meant"
    else:
        assert key not in err, f"the real key {key!r} was reported as unknown"
    assert code != 2, "a config file must be warned about, not refused"
