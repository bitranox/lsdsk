"""Parse and apply ``--set SECTION.KEY=VALUE`` CLI overrides to Config."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import orjson
from lib_layered_config import Config
from pydantic import BaseModel, ConfigDict

from .known_keys import nearest_known_key, nearest_owned_section, unknown_owned_key

if TYPE_CHECKING:
    from lib_layered_config.domain.config import SourceInfo


class OverrideLayer(StrEnum):
    """The provenance layer name ``--set`` introduces, which is lsdsk's alone.

    ``lib_layered_config.Layer`` enumerates the library's own layers -
    ``defaults``, ``app``, ``host``, ``user``, ``dotenv``, ``env`` - each one
    naming a file lsdsk read or an environment variable it consulted. A
    ``--set`` value comes from neither: it was typed on the command line and
    there is no file to point at, so it needs a name the library does not
    already define. This enum owns exactly that one name, not the library's.

    Example:
        >>> OverrideLayer.CLI == "cli"
        True
    """

    CLI = "cli"


CoercedValue = str | int | float | bool | None | list[object] | dict[str, object]
"""Union of types that :func:`coerce_value` can produce."""


class ConfigOverride(BaseModel):
    """A single parsed configuration override, straight from ``--set``.

    Parsed once, immediately, by :func:`parse_override`; nothing downstream
    reaches back into the raw ``SECTION.KEY=VALUE`` string.

    Attributes:
        section: The top-level configuration section the override targets.
        key_path: The dotted path under that section, one element per
            component after the first dot.
        value: The override's value, already coerced by :func:`coerce_value`.

    Example:
        >>> ConfigOverride(section="s", key_path=("k",), value=1)
        ConfigOverride(section='s', key_path=('k',), value=1)
    """

    model_config = ConfigDict(frozen=True)

    section: str
    key_path: tuple[str, ...]
    value: CoercedValue


def parse_override(raw: str) -> ConfigOverride:
    """Split a ``SECTION.KEY[.SUBKEY...]=VALUE`` string into a ConfigOverride.

    The first dot separates the top-level section from the key path.
    The first ``=`` separates the full dotted path from the value.
    Values are coerced via :func:`coerce_value`.

    Args:
        raw: Raw override string (e.g., ``lib_log_rich.console_level=DEBUG``).

    Returns:
        Parsed ConfigOverride with section, key_path tuple, and coerced value.

    Raises:
        ValueError: If the string lacks ``=``, has no dot in the key, or has
            empty section/key components.

    Examples:
        >>> override = parse_override("lib_log_rich.console_level=DEBUG")
        >>> override.section
        'lib_log_rich'
        >>> override.key_path
        ('console_level',)
        >>> override.value
        'DEBUG'

        >>> override = parse_override("lib_log_rich.payload_limits.max_chars=8192")
        >>> override.key_path
        ('payload_limits', 'max_chars')
        >>> override.value
        8192
    """
    if "=" not in raw:
        raise ValueError(f"Invalid override {raw!r}: must contain '='")

    path_part, value_str = raw.split("=", maxsplit=1)

    if "." not in path_part:
        raise ValueError(f"Invalid override {raw!r}: key must contain at least one dot (SECTION.KEY)")

    parts = path_part.split(".")
    section = parts[0]
    key_parts = tuple(parts[1:])

    if not section:
        raise ValueError(f"Invalid override {raw!r}: section name is empty")
    if not all(key_parts):
        raise ValueError(f"Invalid override {raw!r}: key path contains empty component")

    return ConfigOverride(
        section=section,
        key_path=key_parts,
        value=coerce_value(value_str),
    )


def coerce_value(raw: str) -> CoercedValue:
    """Coerce a raw string value using JSON parsing with string fallback.

    Attempts ``orjson.loads`` first (handling booleans, numbers, null, arrays,
    objects). Falls back to the raw string if JSON parsing fails.

    Args:
        raw: Raw value string from CLI.

    Returns:
        Parsed Python value (bool, int, float, None, list, dict) or the
        original string.

    Examples:
        >>> coerce_value("true")
        True
        >>> coerce_value("42")
        42
        >>> coerce_value("3.14")
        3.14
        >>> coerce_value("null")
        >>> coerce_value('["a","b"]')
        ['a', 'b']
        >>> coerce_value("DEBUG")
        'DEBUG'
        >>> coerce_value("")
        ''
    """
    if raw == "":
        return ""
    try:
        return orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError):
        return raw


def _nest_override(target: dict[str, dict[str, object]], override: ConfigOverride) -> None:
    """Build a nested override dict from a parsed ConfigOverride.

    Creates intermediate dicts as needed. The resulting dict structure
    is passed to ``Config.with_overrides()`` for merge.

    Args:
        target: Mutable override dictionary being built.
        override: Parsed override containing section, key_path, and value.

    Examples:
        >>> d: dict[str, dict[str, object]] = {}
        >>> _nest_override(d, ConfigOverride(section="s", key_path=("a",), value=2))
        >>> d["s"]["a"]
        2
        >>> d2: dict[str, dict[str, object]] = {}
        >>> _nest_override(d2, ConfigOverride(section="new", key_path=("x", "y"), value=3))
        >>> d2["new"]["x"]["y"]
        3
    """
    node: dict[str, object] = target.setdefault(override.section, {})
    for part in override.key_path[:-1]:
        existing = node.setdefault(part, {})
        if not isinstance(existing, dict):
            msg = f"Expected dict at key {part!r}, got {type(existing).__name__}"
            raise TypeError(msg)
        node = cast("dict[str, object]", existing)
    node[override.key_path[-1]] = override.value


def _dotted_keys(data: Mapping[str, object], prefix: str = "") -> Iterator[str]:
    """Yield every leaf key of a nested mapping in dotted form.

    Args:
        data: The nested mapping to walk.
        prefix: Dotted path accumulated by the caller, ending in a dot.

    Yields:
        One dotted path per leaf value.

    Example:
        >>> sorted(_dotted_keys({"a": {"b": 1, "c": {"d": 2}}}))
        ['a.b', 'a.c.d']
    """
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            yield from _dotted_keys(cast("Mapping[str, object]", value), f"{path}.")
        else:
            yield path


def _provenance_naming_the_cli(
    config: Config, merged_as_dict: Mapping[str, object], overridden: frozenset[str]
) -> dict[str, SourceInfo]:
    """Copy a Config's provenance, relabelling the keys an override replaced.

    Args:
        config: The Config the values came from, holding the original map.
        merged_as_dict: The merged Config's own mapping, walked for the full
            key set. Passed in already computed, because the caller needs the
            same mapping to build the merged ``Config`` and calling
            ``as_dict()`` a second time here recomputed it for nothing.
        overridden: Dotted keys that ``--set`` supplied.

    Returns:
        A provenance map naming the CLI for overridden keys and the original
        source for every other one.
    """
    provenance: dict[str, SourceInfo] = {}
    for dotted in _dotted_keys(merged_as_dict):
        if dotted in overridden:
            provenance[dotted] = {"layer": OverrideLayer.CLI, "path": None, "key": dotted}
        elif (origin := config.origin(dotted)) is not None:
            provenance[dotted] = origin
    return provenance


def _refusal_for(raw: str, override: ConfigOverride) -> str:
    """Explain a key this tool does not read, naming the one that was probably meant.

    Args:
        raw: The override exactly as typed, so the reader can find it in their
            own command line.
        override: The parsed form, which already carries the section and the key
            path - the dotted string is built from it here rather than taken as
            a second parameter and split back apart, which was re-deriving what
            the caller had.

    Returns:
        The message to refuse with.
    """
    key = ".".join(override.key_path)
    suggestion = nearest_known_key(override.section, override.key_path[-1]) if override.key_path else None
    meant = f" Did you mean {override.section}.{suggestion}?" if suggestion else ""
    return f"Invalid override {raw!r}: [{override.section}] has no key {key!r}.{meant}"


def _section_refusal_for(raw: str, override: ConfigOverride, meant: str) -> str:
    """Explain a section name that is one typo away from one this tool owns.

    Args:
        raw: The override exactly as typed, so the reader can find it in their
            own command line.
        override: The parsed form, which carries the section that was typed.
        meant: The owned section it is probably a typo for.

    Returns:
        The message to refuse with.
    """
    return f"Invalid override {raw!r}: there is no section [{override.section}]. Did you mean [{meant}]?"


def apply_overrides(config: Config, raw_overrides: tuple[str, ...]) -> Config:
    """Deep-merge CLI overrides into a Config instance.

    Parses each raw override string, builds a nested override dict,
    and delegates to ``Config.with_overrides()`` for the merge.

    Args:
        config: Original immutable Config from file/env layers.
        raw_overrides: Tuple of ``SECTION.KEY=VALUE`` strings from ``--set``.

    Returns:
        New Config instance with overrides applied, or the original if
        ``raw_overrides`` is empty.

    Raises:
        ValueError: If any override string is malformed, names a SECTION one
            typo away from an owned one, or names a key that an owned section
            does not have. The owned sections are the three whose key set is
            exactly a model's, so an unknown key there is a typo and inert -
            which reads identically to a setting that was applied. The section
            is judged first because the key check cannot see a wrong one: it
            answers ``None`` for every unowned section, which is what a
            misspelled owned section looks like to it. A section resembling
            nothing owned, and keys outside the three, belong to libraries or to
            another consumer's file and are passed through untouched; see
            :mod:`lsdsk.adapters.config.known_keys`.

    Examples:
        >>> from lib_layered_config import Config
        >>> cfg = Config({"s": {"k": 1}}, {"s.k": {"layer": "default", "path": None, "key": "s.k"}})
        >>> result = apply_overrides(cfg, ("s.k=2",))
        >>> result["s"]["k"]
        2
        >>> result.origin("s.k")["layer"] == "cli"
        True
        >>> apply_overrides(cfg, ()) is cfg
        True
    """
    if not raw_overrides:
        return config

    overrides: dict[str, dict[str, object]] = {}
    overridden: set[str] = set()
    for raw in raw_overrides:
        parsed = parse_override(raw)
        dotted = ".".join((parsed.section, *parsed.key_path))
        # The section is judged FIRST, because the key check cannot see a wrong
        # one: unknown_owned_key answers None for every section this tool does
        # not own, which is what a misspelled owned section looks like to it.
        meant = nearest_owned_section(parsed.section)
        if meant is not None:
            raise ValueError(_section_refusal_for(raw, parsed, meant))
        if unknown_owned_key(parsed.section, parsed.key_path) is not None:
            raise ValueError(_refusal_for(raw, parsed))
        _nest_override(overrides, parsed)
        overridden.add(dotted)

    # Rebuilt rather than returned straight from with_overrides, which shares
    # the original provenance map by design: the merged value is the CLI's and
    # its recorded origin still named the file it replaced, so `lsdsk config`
    # sent a reader to a file holding the old value.
    merged = config.with_overrides(overrides)
    merged_as_dict = merged.as_dict()
    return Config(merged_as_dict, _provenance_naming_the_cli(config, merged_as_dict, frozenset(overridden)))


__all__ = [
    "CoercedValue",
    "ConfigOverride",
    "OverrideLayer",
    "apply_overrides",
    "coerce_value",
    "parse_override",
]
