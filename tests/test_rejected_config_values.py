"""A configuration VALUE this tool cannot use must not pass in silence either.

The key half of this shape is held by ``test_unknown_config_keys.py``. This is the
value half, measured before the fix on ``linux-nvme-board``:

    --set thresholds.wear_warning_percent=1   -> 16 findings
    --set thresholds.wear_warning_percent=abc ->  5 findings, the shipped default
    stderr, in both arms                      ->  0 bytes

So a value the coercers refuse is replaced by the shipped default, the run exits 0,
nothing is written to any stream, and ``lsdsk config`` then reports the refused text
back as the value in force. The fallback itself is deliberate and documented and
stays: a malformed threshold must never stop somebody diagnosing a failing drive.
Only its silence is the defect, and ``known_keys.py`` already states the rule it
breaks - a value that is inert reads exactly like a value that was applied.

Every arm here carries its control, because an arm asserting that a bad value is
named would pass just as well against a build that warns about every value it reads.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.history import read_history_settings
from lsdsk.adapters.config.tunables import read_display_settings, read_thresholds
from lsdsk.adapters.config.values import (
    RejectedValue,
    accepts_flag,
    accepts_positive_float,
    accepts_positive_int,
    accepts_tree_density,
    flag,
    positive_float,
    positive_int,
    tree_density_of,
)
from lsdsk.domain.enums import TreeDensity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result
    from lib_layered_config import Config


class _Reading(Protocol):
    """What the three section readers have in common: the values, and what they refused."""

    @property
    def rejected(self) -> tuple[RejectedValue, ...]:
        """Every value in this section the tool could not use."""
        ...


#: The capture this file's CLI arms replay, the same one the key half uses: its
#: drives are healthy enough that a lowered wear threshold visibly moves the
#: verdict, which is what makes the control arm mean anything.
_CAPTURE = "linux-nvme-board.json"

#: The awkward inputs each coercer is read over, typed as what a configured value
#: really is. Declared rather than written inline because a bare ``{}`` or ``[]``
#: in a parametrize list infers ``dict[Unknown, Unknown]``, and the container a
#: coercer must refuse is exactly the case worth keeping in the list.
_INT_RAWS: list[object] = [1, 99, 0, -1, True, False, "1", "abc", 2.5, None, {}, []]
_FLOAT_RAWS: list[object] = [1, 3.5, 0, 0.0, -0.5, True, "2.5", None, {}]
_FLAG_RAWS: list[object] = [True, False, 1, 0, "true", "", None, {}]
_DENSITY_RAWS: list[object] = [
    "full",
    "FULL",
    " full ",
    "bogus",
    "",
    TreeDensity.STORAGE_AND_SIBLINGS,
    1,
    None,
    {},
]


def _flag_positionally(raw: object, default: Any) -> bool:
    """``flag`` takes its default by keyword; this lets one loop drive all four coercers."""
    return flag(raw, default=default)


def _capture_path() -> Path:
    """Where the replayed snapshot lives."""
    from pathlib import Path as _Path

    return _Path(__file__).parent / "fixtures" / "hw" / _CAPTURE


def _findings_count(output: str) -> int:
    """How many findings an envelope reports."""
    payload: Any = json.loads(output)
    data: Any = payload["data"]
    findings: Any = data["findings"]
    return len(findings)


def _invoke(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    history: Path,
    *overrides: str,
) -> Result:
    """One real run through the root group, replaying the capture in JSON mode."""
    argv = ["--no-record", "--history-file", str(history)]
    for spec in overrides:
        argv += ["--set", spec]
    argv += ["findings", "--replay", str(_capture_path()), "--format", "json"]
    return cli_runner.invoke(cli_mod.cli, argv, obj=production_factory)


@pytest.mark.os_agnostic
def test_a_value_the_tool_can_use_moves_the_verdict_and_is_reported_nowhere(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """The control for every arm below, and it asserts both halves.

    The mechanism works when the value is usable (the verdict moves), and a usable
    value earns no warning. Without the second half, a build that warned about
    every value it read would satisfy all the arms below.
    """
    plain = _invoke(cli_runner, production_factory, tmp_path / "h.json")
    lowered = _invoke(cli_runner, production_factory, tmp_path / "h.json", "thresholds.wear_warning_percent=1")

    assert _findings_count(lowered.stdout) > _findings_count(plain.stdout), (
        "lowering the wear threshold changed nothing, so this file's arms prove nothing"
    )
    assert "wear_warning_percent" not in lowered.stderr, (
        "a value the tool USED was reported as ignored, so the warning is keyed on reading a value "
        "rather than on refusing one"
    )


@pytest.mark.os_agnostic
def test_a_rejected_value_from_set_names_the_key_the_text_and_the_default_in_force(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """All three, because each answers a different question the reader has.

    The key says which setting did nothing, the text says which of their values was
    refused when several are set, and the default says what judged the machine
    instead - without it the reader knows only that they were ignored.
    """
    result = _invoke(cli_runner, production_factory, tmp_path / "h.json", "thresholds.wear_warning_percent=abc")

    assert "thresholds.wear_warning_percent" in result.stderr, "the refused value named no key"
    assert "abc" in result.stderr, "the warning did not quote the text it refused"
    assert "80" in result.stderr, "the warning did not say which default judged the machine instead"
    assert result.exit_code != 2, "a malformed value must not refuse the run; the fallback is deliberate"


@pytest.mark.os_agnostic
def test_a_rejected_value_leaves_stdout_exactly_what_a_parser_expects(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """The warning is stderr's business in both output modes.

    A caller piping ``--format json`` into a decoder must not have to strip prose,
    which is the contract ``cli_snapshot`` states for its own note.
    """
    result = _invoke(cli_runner, production_factory, tmp_path / "h.json", "display.piped_width=abc")

    assert _findings_count(result.stdout) > 0, (
        "the envelope carries no findings, so this arm would pass on an empty stdout"
    )
    assert "Warning" not in result.stdout, "a warning reached the stream a caller parses"
    assert "Warning" in result.stderr, "the warning went nowhere, so the split proves nothing"


@pytest.mark.os_agnostic
def test_a_rejected_value_is_reported_once_however_often_the_settings_are_read(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """Once per run, not once per read.

    ``resolve_tunables`` is called by the root group and again by every view that
    draws, so a warning emitted where the value is READ would repeat itself a
    different number of times per subcommand.
    """
    result = _invoke(cli_runner, production_factory, tmp_path / "h.json", "display.wwn_width=abc")

    assert result.stderr.count("display.wwn_width") == 1, (
        f"the warning appeared {result.stderr.count('display.wwn_width')} times, not once"
    )


@pytest.mark.os_agnostic
def test_a_rejected_history_value_is_named_as_well(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """The third owned section is read by its own module and must not be the quiet one.

    Covering two of the three sections is the shape that cost the disk page its
    serial and firmware columns: it reads as done and one surface stays silent.
    """
    result = _invoke(cli_runner, production_factory, tmp_path / "h.json", "history.max_samples_per_drive=abc")

    assert "history.max_samples_per_drive" in result.stderr, "a refused history value named no key"


@pytest.mark.os_agnostic
def test_a_refused_path_is_reported_even_when_the_command_line_overrides_it(
    config_factory: Callable[[dict[str, Any]], Config],
    tmp_path: Path,
) -> None:
    """The override decides where history goes; it does not excuse the unusable value.

    Written because the obvious spelling of that resolution hides it. ``path_override
    or values.path(...)`` reads correctly and short-circuits, so on exactly the runs
    that pass ``--history-file`` the file's refused value is never even read, and the
    silence this whole change removes comes back for that one case.

    The sentence must also name the override rather than the state file, or it
    reports a fallback that did not happen.
    """
    override = tmp_path / "elsewhere.json"

    reading = read_history_settings(config_factory({"history": {"path": 3}}), path_override=override)

    assert [r.dotted for r in reading.rejected] == ["history.path"], (
        "an unusable history.path went unreported because the command line overrode it"
    )
    assert str(override) in reading.rejected[0].used, (
        f"the warning named {reading.rejected[0].used!r} rather than the location actually in force"
    )
    assert reading.settings.path == override, "the override must still win"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("section", "key", "bad", "good"),
    [
        pytest.param("display", "piped_width", "abc", 200, id="positive_int"),
        pytest.param("thresholds", "quiet_expected_min", "abc", 2.5, id="positive_float"),
        pytest.param("display", "expand_virtual", "yes", True, id="flag"),
        pytest.param("display", "tree_density", "bogus", "full", id="tree_density"),
        pytest.param("history", "max_samples_per_drive", "abc", 7, id="history positive_int"),
        pytest.param("history", "enabled", "yes", False, id="history flag"),
        pytest.param("history", "path", 3, "", id="history path"),
    ],
)
def test_every_coercer_reports_the_value_it_refuses_and_stays_quiet_on_one_it_takes(
    config_factory: Callable[[dict[str, Any]], Config],
    section: str,
    key: str,
    bad: object,
    good: object,
) -> None:
    """One arm per coercer, each with its own control.

    Parametrized over the readers rather than the predicates, so an unreported key
    is caught wherever the omission lives - the predicate, the reader, or the list
    of keys the reader walks. A coercer covered by no arm here is a value that can
    still fall back in silence.
    """
    readers: dict[str, Callable[[Config], _Reading]] = {
        "thresholds": read_thresholds,
        "display": read_display_settings,
        "history": read_history_settings,
    }
    read = readers[section]

    refused = read(config_factory({section: {key: bad}})).rejected
    taken = read(config_factory({section: {key: good}})).rejected

    assert [r.dotted for r in refused] == [f"{section}.{key}"], (
        f"{section}.{key}={bad!r} was refused and reported as {[r.dotted for r in refused]}"
    )
    assert taken == (), f"{section}.{key}={good!r} is usable and was reported as {[r.dotted for r in taken]}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("accepts", "coerce", "defaults", "raws"),
    [
        pytest.param(
            accepts_positive_int,
            positive_int,
            (80, 95),
            _INT_RAWS,
            id="positive_int",
        ),
        pytest.param(
            accepts_positive_float,
            positive_float,
            (1.5, 2.5),
            _FLOAT_RAWS,
            id="positive_float",
        ),
        pytest.param(
            accepts_flag,
            _flag_positionally,
            (True, False),
            _FLAG_RAWS,
            id="flag",
        ),
        pytest.param(
            accepts_tree_density,
            tree_density_of,
            (TreeDensity.STORAGE_ONLY, TreeDensity.FULL),
            _DENSITY_RAWS,
            id="tree_density",
        ),
    ],
)
def test_a_coercer_and_the_test_that_decides_what_it_accepts_cannot_disagree(
    accepts: Callable[[object], bool],
    coerce: Callable[[object, Any], object],
    defaults: tuple[Any, Any],
    raws: list[object],
) -> None:
    """One predicate decides acceptance, and the warning reads the same one.

    Without this the two paths are two lists: a coercer widened to take a new shape
    while its predicate is not would go on warning about a value it now uses, and a
    green suite would say nothing.

    Read under TWO different defaults, which is what makes the property checkable
    without a per-coercer conversion: an accepted value is decided by the raw, so it
    answers the same both times, and a refused one is decided by the default, so it
    follows whichever default it was given. Asserted over each coercer's own awkward
    inputs - a bool where an int is wanted, a numeric string, the enum member itself.
    """
    first, second = defaults
    for raw in raws:
        answers = (coerce(raw, first), coerce(raw, second))
        if accepts(raw):
            assert answers[0] == answers[1], (
                f"{raw!r} is accepted, so the raw value must decide, yet the answer followed the default: {answers}"
            )
        else:
            assert answers == defaults, f"{raw!r} is refused, so the default must decide, yet the answer was {answers}"


@pytest.mark.os_agnostic
def test_a_span_the_rules_cannot_divide_by_is_refused_by_the_value_object() -> None:
    """The adapter's guard is one layer, and the domain has to hold its own.

    ``_rising`` divides by the span once it clears ``min_span_hours``, and the
    threshold's own docstring says "Below one there is nothing to divide by" -
    but nothing on the model said so, and removing the adapter's ``raw > 0``
    SURVIVED the whole suite. Every configured path is guarded now, and a
    direct construction - what the domain's own callers and any embedder do -
    was not, so a zero reached the divisor.
    """
    from pydantic import ValidationError

    from lsdsk.domain.thresholds import Thresholds

    with pytest.raises(ValidationError):
        Thresholds(min_span_hours=0)
    with pytest.raises(ValidationError):
        Thresholds(min_span_hours=-1)
    with pytest.raises(ValidationError):
        Thresholds(quiet_expected_min=0.0)

    # The control: the shipped figures, and the smallest span a rate can be
    # computed over, are still accepted.
    assert Thresholds(min_span_hours=1).min_span_hours == 1
    assert Thresholds().quiet_expected_min == 10.0


@pytest.mark.os_agnostic
def test_the_adapter_guard_the_domain_floor_stands_behind_is_itself_tested() -> None:
    """``positive_int`` refusing zero was the only thing between a file and the traceback."""
    assert positive_int(0, 5) == 5, "a zero span reached the divisor"
    assert positive_int(-3, 5) == 5
    assert positive_int(2, 5) == 2, "the control: a usable value is still taken"
