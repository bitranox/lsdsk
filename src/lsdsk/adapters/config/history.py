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
from typing import TYPE_CHECKING, Any, cast

from ...domain.base import DomainModel
from ..history.store import MAX_SAMPLES_PER_DRIVE, default_history_path

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


def _positive_int(raw: object, default: int) -> int:
    """Read a count, falling back rather than failing the run.

    A malformed cap in a config file must not stop somebody diagnosing a failing
    drive, and a zero or negative one would thin every series to nothing.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return raw if raw > 0 else default


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
    # Read where the section becomes a model, not through a shared accessor that
    # would be a dict interface between modules. A layered-config value is Any by
    # nature; the isinstance check is what makes the cast true.
    raw: object = config.get(SECTION, {})
    section = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    configured = section.get("path")
    # An empty string is how the shipped default says "use the state directory";
    # treating it as a path would write a file literally named "" instead.
    from_file = Path(configured).expanduser() if isinstance(configured, str) and configured.strip() else None
    enabled = section.get("enabled", True)
    return HistorySettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        path=path_override or from_file or default_history_path(),
        max_samples_per_drive=_positive_int(section.get("max_samples_per_drive"), MAX_SAMPLES_PER_DRIVE),
    )


__all__ = ["SECTION", "HistorySettings", "get_history_settings"]
