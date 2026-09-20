"""Whether a named profile answered, and which ones exist to be named.

A profile REPLACES the configuration directories rather than adding to them, so
a name with one letter wrong, or the right letters in the wrong case, reads no
file at all and every value falls back to the shipped one. That is inert, and
this module exists for the same reason :mod:`.known_keys` does: an inert value
must not read like an applied one. ``lsdsk config`` reports the fallen-back
figures as the values in force, so without a word on stderr a reader gets
positive confirmation of a setting that decided nothing.

The two questions are answered from two different places on purpose.

Whether a profile answered is read from PROVENANCE - which files the run
actually loaded - because a directory that exists and holds nothing readable is
a silent no-op in exactly the same way as one that is not there.

Which profiles exist is read from the DIRECTORY the loader would search,
because with a wrong profile nothing from it is in the provenance to learn
from.

System Role:
    Adapter layer, configuration.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

# The one place this package reaches past lib_layered_config's top-level
# exports. The public surface has no way to ask "which directories would you
# search", and nothing else can answer which profiles exist on this machine:
# with a wrong profile the loader reads none of them, so there is no provenance
# to learn it from. Typed, and covered by the tests beside this module.
from lib_layered_config.adapters.path_resolvers.default import DefaultPathResolver

from lsdsk import __init__conf__

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from lib_layered_config import Config

#: The directory the loader inserts for a profile, from lib_layered_config's own
#: path resolver: ``<config root>/profile/<name>/``.
PROFILE_SEGMENT = "profile"

#: How close a name has to be before naming it is help rather than a guess.
#: The same figure the unknown-key suggestion uses, for the same reason.
_SUGGESTION_CUTOFF = 0.6


def _resolver(profile: str | None) -> DefaultPathResolver:
    """The loader's own path resolver, built the way the loader builds it."""
    return DefaultPathResolver(
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        profile=profile,
    )


def _profile_root() -> Path | None:
    """The directory this user's profiles live in.

    Taken from the resolver's ``.env`` path rather than from its config-file
    paths, because those are yielded only when the file already EXISTS - and a
    machine whose only configuration is inside a profile has none of them, which
    is exactly the machine a suggestion is wanted on. The ``.env`` path is
    returned whether or not anything is there, and it sits in the same directory
    the config files would, so its parent is the configuration root on every
    platform the library supports.

    Only the user's own root. A profile installed system-wide is not suggested,
    and the warning still fires for it; a suggestion naming a directory the
    reader cannot see would be the less useful half of this anyway.

    Returns:
        The directory, or ``None`` when this platform's strategy offers no
        ``.env`` location to derive one from. The warning then goes out without
        a suggestion rather than with a guessed one.
    """
    strategy = _resolver(None).strategy
    dotenv = None if strategy is None else strategy.dotenv_path()
    return None if dotenv is None else dotenv.parent / PROFILE_SEGMENT


def existing_profiles() -> tuple[str, ...]:
    """The profile names this machine has a directory for, in sorted order.

    A directory with nothing readable in it still counts: somebody made it and
    meant that name, so it is worth suggesting even though a run using it would
    still be told it loaded nothing.

    Returns:
        Each profile name once, sorted, so the same machine always lists them
        the same way.
    """
    root = _profile_root()
    if root is None:
        return ()
    try:
        entries = list(root.iterdir())
    except OSError:
        # An unreadable or absent config root is not an error here: it means
        # this machine has no profiles under it, which is the usual case.
        return ()
    return tuple(sorted(entry.name for entry in entries if entry.is_dir()))


def contributed_layers(config: Config, profile: str) -> tuple[str, ...]:
    """The files this run loaded from `profile`, empty when it loaded none.

    Args:
        config: The configuration as loaded, with its provenance.
        profile: The name that was asked for.

    Returns:
        Each contributing path once, in the order the keys were seen. Empty
        means the name answered nothing and every value came from elsewhere.
    """
    wanted = (PROFILE_SEGMENT, profile)
    paths: list[str] = []
    # Every DOTTED key, not Config.keys(), which yields the top-level section
    # names alone - and provenance is recorded per dotted key, so asking it
    # about a section name returns None for every one of them and this function
    # reported that no layer had contributed however many had.
    for key in _dotted_keys(config.as_dict()):
        origin = config.origin(key)
        path = None if origin is None else origin.get("path")
        if not isinstance(path, str) or path in paths:
            continue
        parts = Path(path).parts
        if any(parts[index : index + 2] == wanted for index in range(len(parts) - 1)):
            paths.append(path)
    return tuple(paths)


def _dotted_keys(mapping: Mapping[str, object], prefix: str = "") -> Iterator[str]:
    """Every leaf of a nested configuration mapping, as a dotted key."""
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            yield from _dotted_keys(cast("Mapping[str, object]", value), f"{dotted}.")
        else:
            yield dotted


def nearest_profile(typed: str, known: Iterable[str]) -> str | None:
    """The profile `typed` most likely meant, when one is close enough.

    Case is folded before comparing, because a profile directory is matched
    exactly and ``PROD`` for ``prod`` is the near miss a reader is least likely
    to spot by reading their own command back.

    Args:
        typed: What was asked for.
        known: The profiles that exist.

    Returns:
        The closest one as it is spelled on disk, or ``None`` when nothing is
        near enough that naming it would be a guess.

    Example:
        >>> nearest_profile("prodd", ("prod", "staging"))
        'prod'
        >>> nearest_profile("PROD", ("prod", "staging"))
        'prod'
        >>> nearest_profile("nothing-like-it", ("prod", "staging")) is None
        True
    """
    by_folded = {name.casefold(): name for name in known}
    matches = difflib.get_close_matches(typed.casefold(), sorted(by_folded), n=1, cutoff=_SUGGESTION_CUTOFF)
    return by_folded[matches[0]] if matches else None


__all__ = [
    "PROFILE_SEGMENT",
    "contributed_layers",
    "existing_profiles",
    "nearest_profile",
]
