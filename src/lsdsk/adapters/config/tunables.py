"""The `[thresholds]` and `[display]` sections, parsed into typed models.

Every value the tool judges or lays out by is a choice somebody may need to make
differently, so each one is a configuration key with the shipped figure as its
default. What is deliberately absent is anything a specification fixes: register
offsets, IOCTL codes, the Kelvin offset, the 8b/10b encoding divisor and the
512-byte sector are not choices, and a configuration file that could change them
would break decoding rather than tune it.

System Role:
    Adapter-layer configuration parsing. The domain takes a ``Thresholds`` and
    reads nothing itself; the renderers take a ``DisplaySettings``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict

from ...domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

if TYPE_CHECKING:
    from lib_layered_config import Config

THRESHOLDS_SECTION = "thresholds"
DISPLAY_SECTION = "display"

# Terminal width assumed when output is not a terminal, as in a pipe or a CI
# log. Wide enough for every column, so redirected output loses nothing.
DEFAULT_PIPED_WIDTH = 120

# How many findings the one-line verdict names before saying "and N more".
DEFAULT_SUMMARY_LIMIT = 6

# Wear below this, with no measurable rate, earns no row in the trend view:
# every healthy drive wears and a young one has nothing to plan around yet.
DEFAULT_WEAR_ROW_FLOOR_PERCENT = 10

# Generic temperature bands, used only for a drive that publishes no thresholds
# of its own. A drive's own figures always win.

# Whether an ordinary run lists every kernel-virtual device or tallies them.
# Off by default: a host with forty zvols would otherwise bury the drives the
# view exists to show.
DEFAULT_EXPAND_VIRTUAL = False

# Characters of traceback kept in the short and the --traceback forms.
DEFAULT_TRACEBACK_SUMMARY_LIMIT = 500
DEFAULT_TRACEBACK_VERBOSE_LIMIT = 10_000


class DisplaySettings(BaseModel):
    """How output is laid out and where its cut-offs sit.

    Attributes:
        piped_width: Assumed width when output is not a terminal.
        summary_limit: Findings named in the verdict line before "and N more".
        wear_row_floor_percent: Wear below which the trend view stays quiet.
        expand_virtual: Whether the tree and the disk table list every
            kernel-virtual device rather than tallying them in one line.
        traceback_summary_limit: Characters kept in a short traceback.
        traceback_verbose_limit: Characters kept under ``--traceback``.

    Example:
        >>> DisplaySettings().piped_width
        120
    """

    model_config = ConfigDict(frozen=True)

    piped_width: int = DEFAULT_PIPED_WIDTH
    summary_limit: int = DEFAULT_SUMMARY_LIMIT
    wear_row_floor_percent: int = DEFAULT_WEAR_ROW_FLOOR_PERCENT
    expand_virtual: bool = DEFAULT_EXPAND_VIRTUAL
    traceback_summary_limit: int = DEFAULT_TRACEBACK_SUMMARY_LIMIT
    traceback_verbose_limit: int = DEFAULT_TRACEBACK_VERBOSE_LIMIT


def section_of(config: Config, name: str) -> dict[str, Any]:
    """One configuration table, or an empty one."""
    section: object = config.get(name, {})
    # A layered-config value is Any by nature; the isinstance check is what makes
    # the cast true, and a cast keeps the rest of the line checked.
    return cast("dict[str, Any]", section) if isinstance(section, dict) else {}


def positive_int(raw: object, default: int) -> int:
    """Read a count, falling back rather than failing the run.

    A malformed threshold must never stop somebody diagnosing a failing drive,
    and a zero or negative one would make a rule fire on everything or nothing.
    ``True`` is an ``int`` in Python, so booleans are excluded explicitly.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return raw if raw > 0 else default


def positive_float(raw: object, default: float) -> float:
    """Read a rate or a count that may be fractional, falling back if malformed."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return float(raw) if raw > 0 else default


def flag(raw: object, *, default: bool) -> bool:
    """Read a switch, falling back rather than failing the run.

    A string is not accepted as a truthy value: TOML has a real boolean, and
    taking ``"false"`` for true is the kind of quiet inversion that makes a
    setting look ignored.
    """
    return raw if isinstance(raw, bool) else default


def get_thresholds(config: Config) -> Thresholds:
    """Read the `[thresholds]` section.

    Args:
        config: The merged configuration.

    Returns:
        The judgement values, defaulting to the shipped ones key by key.

    Example:
        >>> from lib_layered_config import Config
        >>> get_thresholds(Config({}, {})).wear_critical_percent
        95
        >>> get_thresholds(Config({"thresholds": {"wear_critical_percent": 90}}, {})).wear_critical_percent
        90
    """
    table = section_of(config, THRESHOLDS_SECTION)
    return Thresholds(
        wear_warning_percent=positive_int(table.get("wear_warning_percent"), DEFAULT_THRESHOLDS.wear_warning_percent),
        wear_critical_percent=positive_int(
            table.get("wear_critical_percent"), DEFAULT_THRESHOLDS.wear_critical_percent
        ),
        crc_errors_significant=positive_int(
            table.get("crc_errors_significant"), DEFAULT_THRESHOLDS.crc_errors_significant
        ),
        mixed_firmware_threshold=positive_int(
            table.get("mixed_firmware_threshold"), DEFAULT_THRESHOLDS.mixed_firmware_threshold
        ),
        wear_projection_min_points=positive_int(
            table.get("wear_projection_min_points"), DEFAULT_THRESHOLDS.wear_projection_min_points
        ),
        quiet_expected_min=positive_float(table.get("quiet_expected_min"), DEFAULT_THRESHOLDS.quiet_expected_min),
        min_span_hours=positive_int(table.get("min_span_hours"), DEFAULT_THRESHOLDS.min_span_hours),
    )


def get_display_settings(config: Config) -> DisplaySettings:
    """Read the `[display]` section.

    Args:
        config: The merged configuration.

    Returns:
        The layout values, defaulting to the shipped ones key by key.

    Example:
        >>> from lib_layered_config import Config
        >>> get_display_settings(Config({"display": {"piped_width": 200}}, {})).piped_width
        200
    """
    table = section_of(config, DISPLAY_SECTION)
    return DisplaySettings(
        piped_width=positive_int(table.get("piped_width"), DEFAULT_PIPED_WIDTH),
        summary_limit=positive_int(table.get("summary_limit"), DEFAULT_SUMMARY_LIMIT),
        wear_row_floor_percent=positive_int(table.get("wear_row_floor_percent"), DEFAULT_WEAR_ROW_FLOOR_PERCENT),
        expand_virtual=flag(table.get("expand_virtual"), default=DEFAULT_EXPAND_VIRTUAL),
        traceback_summary_limit=positive_int(table.get("traceback_summary_limit"), DEFAULT_TRACEBACK_SUMMARY_LIMIT),
        traceback_verbose_limit=positive_int(table.get("traceback_verbose_limit"), DEFAULT_TRACEBACK_VERBOSE_LIMIT),
    )


__all__ = [
    "DEFAULT_EXPAND_VIRTUAL",
    "DEFAULT_PIPED_WIDTH",
    "DEFAULT_SUMMARY_LIMIT",
    "DEFAULT_TRACEBACK_SUMMARY_LIMIT",
    "DEFAULT_TRACEBACK_VERBOSE_LIMIT",
    "DEFAULT_WEAR_ROW_FLOOR_PERCENT",
    "DISPLAY_SECTION",
    "THRESHOLDS_SECTION",
    "DisplaySettings",
    "flag",
    "get_display_settings",
    "get_thresholds",
    "positive_float",
    "positive_int",
    "section_of",
]
