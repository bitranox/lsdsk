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

from typing import TYPE_CHECKING, NamedTuple

from ...domain.base import DomainModel
from ...domain.enums import TreeDensity
from ...domain.thresholds import DEFAULT_THRESHOLDS, Thresholds
from .values import RejectedValue, SectionValues

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

# Most characters the wwn column is ever given. An NVMe WWN runs to a hundred
# characters where a SATA one is twenty, so without a ceiling the single longest
# identifier on the machine sets the width of the column for every row.
DEFAULT_WWN_WIDTH = 24

# How much of the PCI fabric the topology view draws, as the shipped default.
# The least of it: on real hardware four device lines in five are unrelated to
# storage and bury the story this tool exists to tell, so the view opens on
# storage and the bridges above it and says in a line above the tree how to ask
# for the rest.
DEFAULT_TREE_DENSITY = TreeDensity.STORAGE_ONLY

# The share of the window the interactive view's detail panel may take. A third
# is what "the whole record of this row" needs for a drive without pushing the
# table it belongs to off the screen; a reader who wants a quarter changes this
# one key. The panel is only ever this TALL AT MOST - a short record takes the
# room it needs and no more.
DEFAULT_DETAIL_HEIGHT_PERCENT = 33

# Characters of traceback kept in the short and the --traceback forms.
DEFAULT_TRACEBACK_SUMMARY_LIMIT = 500
DEFAULT_TRACEBACK_VERBOSE_LIMIT = 10_000


class DisplaySettings(DomainModel, frozen=True):
    """How output is laid out and where its cut-offs sit.

    Attributes:
        piped_width: Assumed width when output is not a terminal.
        summary_limit: Findings named in the verdict line before "and N more".
        wear_row_floor_percent: Wear below which the trend view stays quiet.
        expand_virtual: Whether the tree and the disk table list every
            kernel-virtual device rather than tallying them in one line.
        wwn_width: Most characters the wwn column is given in either view.
        tree_density: How much of the PCI fabric the topology view draws.
        detail_height_percent: Most of the window the interactive view's detail
            panel may take.
        traceback_summary_limit: Characters kept in a short traceback.
        traceback_verbose_limit: Characters kept under ``--traceback``.

    Example:
        >>> DisplaySettings().piped_width
        120
        >>> f"{DisplaySettings().tree_density}"
        'storage-only'
    """

    piped_width: int = DEFAULT_PIPED_WIDTH
    summary_limit: int = DEFAULT_SUMMARY_LIMIT
    wear_row_floor_percent: int = DEFAULT_WEAR_ROW_FLOOR_PERCENT
    expand_virtual: bool = DEFAULT_EXPAND_VIRTUAL
    wwn_width: int = DEFAULT_WWN_WIDTH
    tree_density: TreeDensity = DEFAULT_TREE_DENSITY
    detail_height_percent: int = DEFAULT_DETAIL_HEIGHT_PERCENT
    traceback_summary_limit: int = DEFAULT_TRACEBACK_SUMMARY_LIMIT
    traceback_verbose_limit: int = DEFAULT_TRACEBACK_VERBOSE_LIMIT


class Tunables(NamedTuple):
    """The judgement and layout values settled for one run.

    They are settled together, from the same three sources in the same order,
    and every view that draws reads both - so they travel as one value rather
    than as two parameters a signature can carry out of step. Lives here rather
    than in the CLI because the render layer reads it too, and a renderer that
    imported a command module to name its own arguments would invert the layers.
    """

    thresholds: Thresholds
    display: DisplaySettings


class ThresholdsReading(NamedTuple):
    """The figures the rules weigh against, and every configured value refused.

    Returned as a pair rather than threaded through a mutable collector because the
    refusals are a PROPERTY of this reading: the value and the record of falling
    back come from the same decision, so neither can be produced without the other.
    """

    thresholds: Thresholds
    rejected: tuple[RejectedValue, ...]


class DisplayReading(NamedTuple):
    """The layout values, and every configured value refused."""

    display: DisplaySettings
    rejected: tuple[RejectedValue, ...]


def read_thresholds(config: Config) -> ThresholdsReading:
    """Read the `[thresholds]` section, keeping what it could not use.

    Args:
        config: The merged configuration.

    Returns:
        The judgement values, defaulting to the shipped ones key by key, and the
        refused values in the order they were read.

    Example:
        >>> from lib_layered_config import Config
        >>> read_thresholds(Config({"thresholds": {"wear_critical_percent": "abc"}}, {})).rejected[0].dotted
        'thresholds.wear_critical_percent'
    """
    # Read where the section becomes a model, not through a shared accessor that
    # would be a dict interface between modules.
    values = SectionValues(THRESHOLDS_SECTION, config)
    thresholds = Thresholds(
        wear_warning_percent=values.positive_int("wear_warning_percent", DEFAULT_THRESHOLDS.wear_warning_percent),
        wear_critical_percent=values.positive_int("wear_critical_percent", DEFAULT_THRESHOLDS.wear_critical_percent),
        crc_errors_significant=values.positive_int("crc_errors_significant", DEFAULT_THRESHOLDS.crc_errors_significant),
        mixed_firmware_threshold=values.positive_int(
            "mixed_firmware_threshold", DEFAULT_THRESHOLDS.mixed_firmware_threshold
        ),
        wear_projection_min_points=values.positive_int(
            "wear_projection_min_points", DEFAULT_THRESHOLDS.wear_projection_min_points
        ),
        quiet_expected_min=values.positive_float("quiet_expected_min", DEFAULT_THRESHOLDS.quiet_expected_min),
        min_span_hours=values.positive_int("min_span_hours", DEFAULT_THRESHOLDS.min_span_hours),
    )
    return ThresholdsReading(thresholds, values.rejected)


def read_display_settings(config: Config) -> DisplayReading:
    """Read the `[display]` section, keeping what it could not use.

    Args:
        config: The merged configuration.

    Returns:
        The layout values, defaulting to the shipped ones key by key, and the
        refused values in the order they were read.

    Example:
        >>> from lib_layered_config import Config
        >>> read_display_settings(Config({"display": {"tree_density": "bogus"}}, {})).rejected[0].raw
        'bogus'
    """
    values = SectionValues(DISPLAY_SECTION, config)
    display = DisplaySettings(
        piped_width=values.positive_int("piped_width", DEFAULT_PIPED_WIDTH),
        summary_limit=values.positive_int("summary_limit", DEFAULT_SUMMARY_LIMIT),
        wear_row_floor_percent=values.positive_int("wear_row_floor_percent", DEFAULT_WEAR_ROW_FLOOR_PERCENT),
        expand_virtual=values.flag("expand_virtual", default=DEFAULT_EXPAND_VIRTUAL),
        wwn_width=values.positive_int("wwn_width", DEFAULT_WWN_WIDTH),
        tree_density=values.tree_density("tree_density", DEFAULT_TREE_DENSITY),
        detail_height_percent=values.positive_int("detail_height_percent", DEFAULT_DETAIL_HEIGHT_PERCENT),
        traceback_summary_limit=values.positive_int("traceback_summary_limit", DEFAULT_TRACEBACK_SUMMARY_LIMIT),
        traceback_verbose_limit=values.positive_int("traceback_verbose_limit", DEFAULT_TRACEBACK_VERBOSE_LIMIT),
    )
    return DisplayReading(display, values.rejected)


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
    return read_thresholds(config).thresholds


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
    return read_display_settings(config).display


__all__ = [
    "DEFAULT_DETAIL_HEIGHT_PERCENT",
    "DEFAULT_EXPAND_VIRTUAL",
    "DEFAULT_PIPED_WIDTH",
    "DEFAULT_SUMMARY_LIMIT",
    "DEFAULT_TRACEBACK_SUMMARY_LIMIT",
    "DEFAULT_TRACEBACK_VERBOSE_LIMIT",
    "DEFAULT_TREE_DENSITY",
    "DEFAULT_WEAR_ROW_FLOOR_PERCENT",
    "DEFAULT_WWN_WIDTH",
    "DISPLAY_SECTION",
    "THRESHOLDS_SECTION",
    "DisplayReading",
    "DisplaySettings",
    "ThresholdsReading",
    "Tunables",
    "get_display_settings",
    "get_thresholds",
    "read_display_settings",
    "read_thresholds",
]
