"""The ``[history]`` configuration section, parsed into a typed model.

Counter history is the one thing lsdsk produces that cannot be rebuilt from the
hardware, so where it lives and whether it is written are worth configuring
rather than hard-coding. Everything here is read once at the boundary and handed
on as a model; no other module reads the raw configuration.

System Role:
    Adapter-layer configuration parsing. The storage rules live in
    ``lsdsk.adapters.history.store``; the trend rules in ``lsdsk.domain.history``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ...domain.base import DomainModel
from ..history.store import MAX_SAMPLES_PER_DRIVE, default_history_path
from .values import RejectedValue, SectionValues

if TYPE_CHECKING:
    from lib_layered_config import Config

SECTION = "history"


class HistorySettings(DomainModel, frozen=True):
    """How counter history behaves on this machine.

    Attributes:
        enabled: Whether an ordinary run records a reading. Turning it off never
            stops history being READ, so findings stay graded against the past.
        path: Where the record is kept, already resolved to a real path.
        max_samples_per_drive: How many readings one drive keeps before the
            middle of its series is thinned.

    Example:
        >>> HistorySettings(path=Path("/tmp/h.json")).enabled
        True
    """

    enabled: bool = True
    path: Path
    max_samples_per_drive: int = MAX_SAMPLES_PER_DRIVE


class HistoryReading(NamedTuple):
    """How counter history behaves, and every configured value refused."""

    settings: HistorySettings
    rejected: tuple[RejectedValue, ...]


def read_history_settings(config: Config, *, path_override: Path | None = None) -> HistoryReading:
    """Read the ``[history]`` section, keeping what it could not use.

    Args:
        config: The merged configuration.
        path_override: A path from the command line, which wins over the file.

    Returns:
        The settings with the store path already resolved, and the refused values
        in the order they were read.

    Example:
        >>> from lib_layered_config import Config
        >>> read_history_settings(Config({"history": {"enabled": "yes"}}, {})).rejected[0].reason
        'not true or false'
    """
    # Read where the section becomes a model, not through a shared accessor that
    # would be a dict interface between modules.
    values = SectionValues(SECTION, config)
    # Read before the choice, never inside it: `path_override or values.path(...)`
    # short-circuits, so on exactly the runs that pass --history-file the file's
    # value would never be read and an unusable one would go unreported again.
    from_file = values.path("path", overridden_by=path_override)
    settings = HistorySettings(
        enabled=values.flag("enabled", default=True),
        path=path_override or from_file or default_history_path(),
        max_samples_per_drive=values.positive_int("max_samples_per_drive", MAX_SAMPLES_PER_DRIVE),
    )
    return HistoryReading(settings, values.rejected)


def get_history_settings(config: Config, *, path_override: Path | None = None) -> HistorySettings:
    """Read the ``[history]`` section.

    Args:
        config: The merged configuration.
        path_override: A path from the command line, which wins over the file.

    Returns:
        The settings, with the store path already resolved.

    Example:
        >>> from lib_layered_config import Config
        >>> get_history_settings(Config({}, {})).enabled
        True
        >>> get_history_settings(Config({"history": {"enabled": False}}, {})).enabled
        False
    """
    return read_history_settings(config, path_override=path_override).settings


__all__ = ["SECTION", "HistoryReading", "HistorySettings", "get_history_settings", "read_history_settings"]
