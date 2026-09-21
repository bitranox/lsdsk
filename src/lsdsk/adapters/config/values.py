"""How a raw configured value becomes a typed one, and what it had to refuse.

A malformed value must never stop somebody diagnosing a failing drive, so every
coercer here falls back to the shipped default rather than failing the run. That
fallback is deliberate; its SILENCE was the defect. ``known_keys.py`` states the
rule for the key half of the same shape - a value that is inert reads exactly like
a value that was applied - and a refused value is inert in exactly that way, with
``lsdsk config`` afterwards reporting the refused text back as the value in force.

So each coercer is written in terms of a named predicate that decides acceptance,
and :class:`SectionValues` reads THAT predicate to record what it fell back on.
One decider, two readers: a coercer widened to take a new shape while its
predicate is not would warn about a value it now uses, which is what
``test_a_coercer_and_the_test_that_decides_what_it_accepts_cannot_disagree`` pins.

System Role:
    Adapter-layer configuration parsing, shared by the sections in
    :mod:`lsdsk.adapters.config.tunables` and :mod:`lsdsk.adapters.config.history`.
    The report itself belongs to the CLI, beside the one for unknown keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ...domain.base import DomainModel
from ...domain.enums import TreeDensity

if TYPE_CHECKING:
    from lib_layered_config import Config

#: What each coercer takes, worded for somebody reading it off a terminal beside
#: the value that was refused. Phrased as what the setting wants rather than what
#: the value is, because the reader already has the value in front of them.
REASON_POSITIVE_INT = "not a whole number above zero, or too large to use"
REASON_POSITIVE_FLOAT = "not a number above zero, or too large to use"
REASON_FLAG = "not true or false"
REASON_PATH = "not a path"

#: What a refused location falls back to, said rather than resolved (see ``path``).
USED_PLATFORM_STATE_FILE = "the per-user state file"


def reason_tree_density() -> str:
    """What a density value must be, naming the members rather than a written list.

    Walked from the enum for the same reason ``TREE_DENSITY_TOKENS`` is: a density
    added to :class:`~lsdsk.domain.enums.TreeDensity` must not leave a hand-kept
    sentence here naming three of four.

    Returns:
        The phrase a refusal uses after the colon.

    Example:
        >>> reason_tree_density()
        'not one of storage-only, storage-and-siblings, full'
    """
    return "not one of " + ", ".join(density.value for density in TreeDensity)


class RejectedValue(DomainModel, frozen=True):
    """One configured value the tool could not use, and what judged the machine instead.

    Attributes:
        dotted: The setting as a reader writes it, ``thresholds.wear_warning_percent``.
        raw: The value that was refused, rendered as they would recognise it.
        reason: What kind of value the setting takes.
        used: The default that is in force instead.

    Example:
        >>> RejectedValue(dotted="display.wwn_width", raw="abc", reason=REASON_POSITIVE_INT, used="24").as_sentence()
        'Warning: ignoring display.wwn_width=abc: not a whole number above zero, or too large to use. Using 24.'
    """

    dotted: str
    raw: str
    reason: str
    used: str

    def as_sentence(self) -> str:
        """The warning, worded once for every surface that reports it."""
        return f"Warning: ignoring {self.dotted}={self.raw}: {self.reason}. Using {self.used}."


def rendered(value: object) -> str:
    """A configured value as a reader would recognise it.

    A string is shown bare, because that is how they typed it after the ``=``;
    anything else gets its repr, so a table or a number is unmistakable.

    Args:
        value: The value as the configuration layer handed it over.

    Returns:
        Text to put after the ``=`` in a warning.

    Example:
        >>> rendered("abc"), rendered(3), rendered({})
        ('abc', '3', '{}')
    """
    return value if isinstance(value, str) else repr(value)


#: The largest magnitude a configured number may carry.
#:
#: This module's whole guarantee is that a value it cannot use falls back rather
#: than failing the run, and an unbounded one breaks that from the other side: a
#: legal 64-bit TOML integer in ``display.wwn_width`` reaches ``str.ljust`` in
#: the layout and asks for an allocation of that size, which is a crash rather
#: than a fallback. Every ``[display]`` width and limit and every ``[thresholds]``
#: count reads through the two coercers below, so one ceiling covers them all.
#:
#: A billion is far above anything a terminal, a percentage, a sample count or a
#: span in hours can mean - a billion hours is 114,000 years - and far above the
#: largest figure the config-key harness drives a key to, which is a hundred
#: million. The history store bounds its own numbers separately and much higher,
#: because what it holds is a decoded hardware counter rather than something
#: somebody typed.
MAX_CONFIGURED_MAGNITUDE = 10**9


def accepts_positive_int(raw: object) -> bool:
    """Whether a configured value is usable as a count.

    ``True`` is an ``int`` in Python, so booleans are excluded explicitly; zero and
    below would make a rule fire on everything or on nothing.

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.

    Example:
        >>> accepts_positive_int(1), accepts_positive_int(0), accepts_positive_int(True)
        (True, False, False)
        >>> accepts_positive_int(10**9 + 1)
        False
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and 0 < raw <= MAX_CONFIGURED_MAGNITUDE


def positive_int(raw: object, default: int) -> int:
    """Read a count, falling back rather than failing the run.

    Args:
        raw: The configured value.
        default: The shipped figure to use when it cannot be.

    Returns:
        The configured count, or ``default``.
    """
    return cast("int", raw) if accepts_positive_int(raw) else default


def accepts_positive_float(raw: object) -> bool:
    """Whether a configured value is usable as a rate or a fractional count.

    Example:
        >>> accepts_positive_float(2.5), accepts_positive_float("2.5")
        (True, False)
        >>> accepts_positive_float(1e30)
        False

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.
    """
    return not isinstance(raw, bool) and isinstance(raw, (int, float)) and 0 < raw <= MAX_CONFIGURED_MAGNITUDE


def positive_float(raw: object, default: float) -> float:
    """Read a rate or a count that may be fractional, falling back if malformed.

    Args:
        raw: The configured value.
        default: The shipped figure to use when it cannot be.

    Returns:
        The configured number, or ``default``.
    """
    return float(cast("float", raw)) if accepts_positive_float(raw) else default


def accepts_flag(raw: object) -> bool:
    """Whether a configured value is usable as a switch.

    A string is not: TOML has a real boolean, and taking ``"false"`` for true is the
    kind of quiet inversion that makes a setting look ignored.

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.

    Example:
        >>> accepts_flag(False), accepts_flag("false"), accepts_flag(1)
        (True, False, False)
    """
    return isinstance(raw, bool)


def flag(raw: object, *, default: bool) -> bool:
    """Read a switch, falling back rather than failing the run.

    Args:
        raw: The configured value.
        default: The shipped setting to use when it cannot be.

    Returns:
        The configured switch, or ``default``.
    """
    return cast("bool", raw) if accepts_flag(raw) else default


def accepts_tree_density(raw: object) -> bool:
    """Whether a configured value names a density this tool draws.

    Example:
        >>> accepts_tree_density("Full"), accepts_tree_density("bogus")
        (True, False)

    Args:
        raw: The configured value, either a member or the name of one.

    Returns:
        Whether it names a density this tool draws.
    """
    if isinstance(raw, TreeDensity):
        return True
    return isinstance(raw, str) and raw.strip().casefold() in {density.value for density in TreeDensity}


def tree_density_of(raw: object, default: TreeDensity) -> TreeDensity:
    """Read a density name, falling back rather than failing the run.

    The fallback is passed in rather than defaulted here, so the shipped figure has
    exactly one home - ``tunables.DEFAULT_TREE_DENSITY`` - and a second copy cannot
    drift from it.

    Example:
        >>> shipped = TreeDensity.STORAGE_ONLY
        >>> f"{tree_density_of(' FULL ', shipped)}", f"{tree_density_of('bogus', shipped)}"
        ('full', 'storage-only')

    Args:
        raw: The configured value, either a member or the name of one.
        default: The shipped density to use when it names none.

    Returns:
        The named density, or ``default``.
    """
    if not accepts_tree_density(raw):
        return default
    if isinstance(raw, TreeDensity):
        return raw
    return TreeDensity(cast("str", raw).strip().casefold())


def accepts_path(raw: object) -> bool:
    """Whether a configured value is usable as a file location.

    Any string is, blank included: an empty string is how the shipped default says
    "use the state directory", so it is a deliberate value rather than a refused one.

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.

    Example:
        >>> accepts_path(""), accepts_path("~/h.json"), accepts_path(3)
        (True, True, False)
    """
    return isinstance(raw, str)


def path_or_none(raw: object) -> Path | None:
    """A configured location, or None to mean "wherever this platform keeps state".

    Example:
        >>> path_or_none("  "), path_or_none(3)
        (None, None)

    Args:
        raw: The configured value.

    Returns:
        The expanded location, or ``None`` to mean the platform's own.
    """
    if not accepts_path(raw) or not cast("str", raw).strip():
        return None
    return Path(cast("str", raw)).expanduser()


class SectionValues:
    """One configuration section's raw table, remembering what its coercers refused.

    Each method pairs a coercer with the predicate that decides what the coercer
    takes, so the value and the record of refusing it come from one decision. A key
    that is ABSENT is not a refusal: the whole section is optional and its defaults
    are the shipped figures.

    Example:
        >>> from lib_layered_config import Config
        >>> values = SectionValues("display", Config({"display": {"wwn_width": "abc"}}, {}))
        >>> values.positive_int("wwn_width", 24)
        24
        >>> values.rejected[0].as_sentence()
        'Warning: ignoring display.wwn_width=abc: not a whole number above zero, or too large to use. Using 24.'
    """

    def __init__(self, section: str, config: Config) -> None:
        """Take the section's table, or an empty one when it is absent or not a table."""
        raw: object = config.get(section, {})
        self._section = section
        # A layered-config value is Any by nature; the isinstance check is what
        # makes the cast true.
        self._table = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        self._rejected: list[RejectedValue] = []

    @property
    def rejected(self) -> tuple[RejectedValue, ...]:
        """Every value in this section the tool could not use, in the order read."""
        return tuple(self._rejected)

    def positive_int(self, key: str, default: int) -> int:
        """A count, recording the refusal if the configured value is not one."""
        raw = self._table.get(key)
        if key in self._table and not accepts_positive_int(raw):
            self._note(key, reason=REASON_POSITIVE_INT, used=default)
        return positive_int(raw, default)

    def positive_float(self, key: str, default: float) -> float:
        """A rate, recording the refusal if the configured value is not one."""
        raw = self._table.get(key)
        if key in self._table and not accepts_positive_float(raw):
            self._note(key, reason=REASON_POSITIVE_FLOAT, used=default)
        return positive_float(raw, default)

    def flag(self, key: str, *, default: bool) -> bool:
        """A switch, recording the refusal if the configured value is not one."""
        raw = self._table.get(key)
        if key in self._table and not accepts_flag(raw):
            self._note(key, reason=REASON_FLAG, used=default)
        return flag(raw, default=default)

    def tree_density(self, key: str, default: TreeDensity) -> TreeDensity:
        """A density name, recording the refusal if the configured value is not one."""
        raw = self._table.get(key)
        if key in self._table and not accepts_tree_density(raw):
            self._note(key, reason=reason_tree_density(), used=default)
        return tree_density_of(raw, default)

    def path(self, key: str, *, overridden_by: Path | None = None) -> Path | None:
        """A location, or None for the platform's own, recording a value that is neither.

        ``overridden_by`` is what the command line asked for, and it is taken rather
        than defaulted so the warning names what really ends up in force: without it
        a refused path would be reported as falling back to the state file on a run
        that was told to use somewhere else, which is a false sentence.

        With no override the fallback is named in PROSE rather than resolved, because
        resolving the platform's state directory to put it in a warning would resolve
        it on every run, including the runs that never need it - it is the caller's
        last fallback and stays lazy.
        """
        raw = self._table.get(key)
        if key in self._table and not accepts_path(raw):
            used = rendered(overridden_by) if overridden_by is not None else USED_PLATFORM_STATE_FILE
            self._note(key, reason=REASON_PATH, used=used)
        return path_or_none(raw)

    def _note(self, key: str, *, reason: str, used: object) -> None:
        """Record that this key's configured value was refused."""
        self._rejected.append(
            RejectedValue(
                dotted=f"{self._section}.{key}",
                raw=rendered(self._table[key]),
                reason=reason,
                used=rendered(used),
            )
        )


__all__ = [
    "REASON_FLAG",
    "REASON_PATH",
    "REASON_POSITIVE_FLOAT",
    "REASON_POSITIVE_INT",
    "USED_PLATFORM_STATE_FILE",
    "RejectedValue",
    "SectionValues",
    "accepts_flag",
    "accepts_path",
    "accepts_positive_float",
    "accepts_positive_int",
    "accepts_tree_density",
    "flag",
    "path_or_none",
    "positive_float",
    "positive_int",
    "reason_tree_density",
    "rendered",
    "tree_density_of",
]
