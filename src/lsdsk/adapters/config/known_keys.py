"""Which configuration keys this tool actually reads.

Purpose
-------
A key nobody reads is inert, and a value that is inert reads exactly like a value
that was applied. ``--set thresholds.wear_warnning_percent=1`` and the same
typo in a config file both exit 0, print nothing, and leave the tool judging by
the shipped default - so an operator who believes they lowered the wear warning
to 1 percent gets the default verdict and no signal that their setting did
nothing. For a tool whose whole output is a judgement, that is the setting
silently deciding the answer.

Why only three sections
-----------------------
The shipped defaults declare seven sections, and lsdsk owns the key set of only
three of them: ``thresholds``, ``display`` and ``history`` are exactly the
fields of :class:`~lsdsk.domain.thresholds.Thresholds`,
:class:`~lsdsk.adapters.config.tunables.DisplaySettings` and
:class:`~lsdsk.adapters.config.history.HistorySettings`, and
``test_every_key_documented_in_the_shipped_toml_exists_on_its_model`` holds the
file and the model together in BOTH directions. Inside those three an unknown
key is a typo and nothing else.

The rest belong to libraries - ``lib_layered_config``, ``lib_log_rich`` - which
accept keys this project never ships a line for, and a top-level key may come
from a ``.env`` that other tooling also reads. Refusing there would turn a
forward-compatible file into a hard failure over a key that is somebody else's
business, so this module says nothing about them. Closing the case that can be
proven wrong beats guessing at the case that cannot.

Contents
--------
* :func:`owned_sections` - the sections whose keys are fully known, and their keys
* :func:`unknown_owned_key` - the dotted path if it names an unknown key, else None
* :func:`nearest_known_key` - the key a reader most likely meant
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, cast

from lsdsk.domain.thresholds import Thresholds

from .history import HistorySettings
from .tunables import DisplaySettings

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: How close a suggestion has to be before offering it is help rather than noise.
_SUGGESTION_CUTOFF = 0.6


def owned_sections() -> Mapping[str, frozenset[str]]:
    """The sections whose full key set this tool knows, mapped to those keys.

    Derived from the models rather than listed, so a field added to one of them
    is known here without an edit - the same reason the shipped TOML is checked
    against the models rather than against a second list.

    Returns:
        Section name to the keys that section accepts.

    Example:
        >>> "wear_warning_percent" in owned_sections()["thresholds"]
        True
        >>> "lib_log_rich" in owned_sections()
        False
    """
    return {
        "thresholds": frozenset(Thresholds.model_fields),
        "display": frozenset(DisplaySettings.model_fields),
        "history": frozenset(HistorySettings.model_fields),
    }


def unknown_owned_key(section: str, key_path: Sequence[str]) -> str | None:
    """The dotted path, when it names a key an owned section does not have.

    Args:
        section: The top-level section named by the caller.
        key_path: The remaining path components.

    Returns:
        The full dotted path when it is unknown, or ``None`` when the key is
        known or the section is not one this tool owns. A nested path under an
        owned section is unknown by construction: none of the three models has
        a nested table.

    Example:
        >>> unknown_owned_key("thresholds", ["wear_warnning_percent"])
        'thresholds.wear_warnning_percent'
        >>> unknown_owned_key("thresholds", ["wear_warning_percent"]) is None
        True
        >>> unknown_owned_key("lib_log_rich", ["anything_at_all"]) is None
        True
    """
    known = owned_sections().get(section)
    if known is None:
        return None
    dotted = ".".join((section, *key_path))
    if len(key_path) == 1 and key_path[0] in known:
        return None
    return dotted


def unknown_owned_keys(data: Mapping[str, object]) -> tuple[str, ...]:
    """Every dotted path in `data` naming a key an owned section does not have.

    Args:
        data: A merged configuration mapping, as ``Config.as_dict`` returns it.

    Returns:
        The offending dotted paths, sorted so the report is stable run to run.
        Sections this tool does not own contribute nothing, whatever they hold.

    Example:
        >>> unknown_owned_keys({"thresholds": {"wear_warnning_percent": 1}})
        ('thresholds.wear_warnning_percent',)
        >>> unknown_owned_keys({"thresholds": {"wear_warning_percent": 1}})
        ()
        >>> unknown_owned_keys({"lib_log_rich": {"whatever": 1}})
        ()
    """
    found: list[str] = []
    for section, known in owned_sections().items():
        table = data.get(section)
        if not isinstance(table, dict):
            continue
        keys = cast("dict[str, object]", table)
        found.extend(f"{section}.{key}" for key in keys if key not in known)
    return tuple(sorted(found))


def nearest_known_key(section: str, key: str) -> str | None:
    """The key in `section` a reader most likely meant, when one is close enough.

    Args:
        section: An owned section.
        key: What was typed.

    Returns:
        The closest known key, or ``None`` when nothing is near enough that
        naming it would be a guess.

    Example:
        >>> nearest_known_key("thresholds", "wear_warnning_percent")
        'wear_warning_percent'
        >>> nearest_known_key("thresholds", "completely_unrelated") is None
        True
    """
    known = owned_sections().get(section)
    if known is None:
        return None
    matches = difflib.get_close_matches(key, sorted(known), n=1, cutoff=_SUGGESTION_CUTOFF)
    return matches[0] if matches else None


__all__ = ["nearest_known_key", "owned_sections", "unknown_owned_key", "unknown_owned_keys"]
