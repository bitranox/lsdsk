"""How a raw configured value becomes a typed one, and what it had to refuse.

A malformed value must never stop somebody diagnosing a failing drive, so every
coercer here falls back to the shipped default rather than failing the run. That
fallback is deliberate; its SILENCE was the defect. The one exception is an
ambiguous integer permission mode (``755``): guessing decimal or octal gives a mode
nobody meant, so :class:`AmbiguousModeError` stops the run at startup instead. ``known_keys.py`` states the
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

from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Final, cast

from ...domain.base import DomainModel
from ...domain.enums import TreeDensity
from ...domain.text import visible_text

if TYPE_CHECKING:
    from lib_layered_config import Config

#: What each coercer takes, worded for somebody reading it off a terminal beside
#: the value that was refused. Phrased as what the setting wants rather than what
#: the value is, because the reader already has the value in front of them.
REASON_POSITIVE_INT = "not a whole number above zero, or too large to use"
REASON_POSITIVE_FLOAT = "not a number above zero, or too large to use"
REASON_PERCENT = "not a whole number from 1 to 100"
REASON_FLAG = "not true or false"
REASON_PATH = "not a path, or names a user this machine does not have"
REASON_MODE = "not a file mode; write it as text: 0755 or 0o755"
REASON_AMBIGUOUS_MODE = (
    "ambiguous: a bare integer of only the digits 0-7 may be octal digits or a numeric mode; "
    "write it as text: 0755 or 0o755"
)

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
        """The warning, worded once for every surface that reports it.

        The refused value is quoted through :func:`~lsdsk.domain.text.visible_text`,
        so a control character in it reaches the terminal as its escape rather
        than as a sequence the terminal would act on. Every value a reader types
        or a file carries is untrusted text at this point.
        """
        return f"Warning: ignoring {self.dotted}={visible_text(self.raw)}: {self.reason}. Using {self.used}."


#: What a section given as a single value is told: the shipped table judges the run instead.
REASON_SECTION: Final = "not a table"


def rendered(value: object) -> str:
    """A configured value as a reader would recognise it.

    A string is shown bare, because that is how they typed it after the ``=``;
    anything else gets its repr, so a table or a number is unmistakable. A bool
    is spelled the way the file and ``--set`` spell it, because Python's
    ``True`` beside a refusal saying "not true or false" is two words for one
    value.

    A path is the exception, and it reaches here as the location that ended up in
    force rather than as something the reader typed. Its repr names the class -
    ``PosixPath('/var/x')`` - so the warning told a reader to look for a file by
    a name no filesystem has. Only Windows caught it: the assertion compared
    ``str(path)`` against the rendered text and passed everywhere the repr
    happened to contain it, which stops being true once the separators differ.

    Args:
        value: The value as the configuration layer handed it over.

    Returns:
        Text to put after the ``=`` in a warning.

    Example:
        >>> rendered("abc"), rendered(3), rendered({})
        ('abc', '3', '{}')
        >>> from pathlib import PurePosixPath
        >>> rendered(PurePosixPath("/var/lib/lsdsk/history.json"))
        '/var/lib/lsdsk/history.json'
        >>> rendered(True), rendered(False)
        ('true', 'false')
    """
    if isinstance(value, str):
        return value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


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


#: A percentage of a whole cannot exceed it.
PERCENT_CEILING = 100


def accepts_percent(raw: object) -> bool:
    """Whether a configured value is usable as a percentage of a whole.

    A fill threshold above 100 could never be reached and one at zero would fire
    on everything, so both ends are refused here rather than left to the model's
    own bounds, which would stop the run instead of falling back.

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.

    Example:
        >>> accepts_percent(1), accepts_percent(100), accepts_percent(101), accepts_percent(0)
        (True, True, False, False)
        >>> accepts_percent(True)
        False
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and 1 <= raw <= PERCENT_CEILING


def percent(raw: object, default: int) -> int:
    """Read a percentage, falling back rather than failing the run.

    Args:
        raw: The configured value.
        default: The shipped figure to use when it cannot be.

    Returns:
        The configured percentage, or ``default``.
    """
    return cast("int", raw) if accepts_percent(raw) else default


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


def _octal_text(text: str) -> int | None:
    """The mode a text spelling names (``"0755"``, ``"755"``, ``"0o755"``), or None."""
    try:
        return int(text.strip(), 8)
    except ValueError:
        return None


def accepts_mode(raw: object) -> bool:
    """Whether a configured value is usable as a file permission mode.

    Text is octal, with or without the ``0o`` prefix. An int is a numeric mode only when
    it cannot be octal digits: lib_layered_config reads an unquoted ``.env`` value ``755``
    as the int 755, which is ambiguous between the octal digits and the decimal mode, so
    an int whose decimal digits are all 0-7 is refused rather than guessed. ``493`` has a
    9 and so stays a numeric mode. A bool is an int in Python and would become mode 1 or 0.

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.

    Example:
        >>> accepts_mode("0755"), accepts_mode("0o755"), accepts_mode(493), accepts_mode(0o750)
        (True, True, True, True)
        >>> accepts_mode(755), accepts_mode(420), accepts_mode(True), accepts_mode("999")
        (False, False, False, False)
    """
    if isinstance(raw, bool):
        return False
    if isinstance(raw, int):
        return raw >= 0 and not set(str(raw)) <= set("01234567")
    return isinstance(raw, str) and _octal_text(raw) is not None


def is_ambiguous_mode(raw: object) -> bool:
    """Whether a configured mode is an int that could be octal digits (``755``, ``644``, ``0``).

    Args:
        raw: The configured value.

    Returns:
        Whether it is such an int; a bool is not one.

    Example:
        >>> is_ambiguous_mode(755), is_ambiguous_mode(493), is_ambiguous_mode("755"), is_ambiguous_mode(True)
        (True, False, False, False)
    """
    return isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 and set(str(raw)) <= set("01234567")


class AmbiguousModeError(ValueError):
    """A permission mode written as a bare int that could be octal digits; the run must not guess.

    Unlike every other refused value this one stops the run, by explicit decision: guessing
    decimal gives a mode nobody meant (755 is 0o1363), guessing octal contradicts the int.

    Attributes:
        dotted: The setting as a reader writes it.
        raw: The refused value.
    """

    def __init__(self, dotted: str, raw: object) -> None:
        """Remember which setting held which value and word the refusal.

        Args:
            dotted: The setting as a reader writes it.
            raw: The refused value.
        """
        self.dotted = dotted
        self.raw = raw
        super().__init__(f"{dotted}={raw!r}: {REASON_AMBIGUOUS_MODE}")


def mode_of(raw: object, default: int) -> int:
    """Read a permission mode, falling back rather than failing the run.

    Args:
        raw: The configured value.
        default: The shipped mode to use when it cannot be.

    Returns:
        The configured mode, or ``default``.

    Example:
        >>> mode_of("0o755", 0o700), mode_of(493, 0o700), mode_of(755, 0o700)
        (493, 493, 448)
    """
    if not accepts_mode(raw):
        return default
    return raw if isinstance(raw, int) else cast("int", _octal_text(cast("str", raw)))


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


def _expanded(text: str) -> Path | None:
    """The location a non-blank configured string names, or None when it names none.

    Two strings name no location however they are used. A NUL byte cannot reach
    any operating system's file API, and ``~name`` for a user this machine does not
    have makes ``expanduser`` raise ``RuntimeError`` - and ``ValueError`` when the
    name itself holds a NUL. Both used to escape the coercer as a traceback and exit
    70, which is the one outcome a configured value must never cause.

    Args:
        text: The configured string, not blank.

    Returns:
        The expanded location, or ``None``.
    """
    if "\x00" in text:
        return None
    try:
        return Path(text).expanduser()
    except (RuntimeError, ValueError):
        return None


def accepts_path(raw: object) -> bool:
    r"""Whether a configured value is usable as a file location.

    Any string that names a location is, blank included: an empty string is how the
    shipped default says "use the state directory", so it is a deliberate value
    rather than a refused one.

    Args:
        raw: The configured value, of whatever type the file produced.

    Returns:
        Whether it can be used as it stands.

    Example:
        >>> accepts_path(""), accepts_path("~/h.json"), accepts_path(3)
        (True, True, False)
        >>> accepts_path("a\x00b")
        False
    """
    if not isinstance(raw, str):
        return False
    return not raw.strip() or _expanded(raw) is not None


def path_or_none(raw: object) -> Path | None:
    r"""A configured location, or None to mean "wherever this platform keeps state".

    Example:
        >>> path_or_none("  "), path_or_none(3), path_or_none("a\x00b")
        (None, None, None)

    Args:
        raw: The configured value.

    Returns:
        The expanded location, or ``None`` to mean the platform's own.
    """
    if not accepts_path(raw) or not cast("str", raw).strip():
        return None
    return _expanded(cast("str", raw))


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
        """Take the section's table, or an empty one when it is absent or not a table.

        A section that is present but not a table (``history = false``) is
        recorded as refused: none of its keys can be read, so the shipped section
        is what judges the run, and that has to be said.
        """
        raw: object = config.get(section, {})
        self._section = section
        # A layered-config value is Any by nature; the isinstance check is what
        # makes the cast true.
        self._table = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        self._rejected: list[RejectedValue] = [] if isinstance(raw, dict) else [self._not_a_table(raw)]

    def _not_a_table(self, raw: object) -> RejectedValue:
        """The refusal for a section given as a single value."""
        return RejectedValue(
            dotted=self._section,
            raw=rendered(raw),
            reason=REASON_SECTION,
            used=f"the shipped [{self._section}] section",
        )

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

    def percent(self, key: str, default: int) -> int:
        """A percentage from 1 to 100, recording the refusal if the configured value is not one."""
        raw = self._table.get(key)
        if key in self._table and not accepts_percent(raw):
            self._note(key, reason=REASON_PERCENT, used=default)
        return percent(raw, default)

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

    def mode(self, key: str, default: int) -> int:
        """A permission mode, recording the refusal if the configured value is not one.

        Raises:
            AmbiguousModeError: For an int that could be octal digits; the one value that stops the run.
        """
        raw = self._table.get(key)
        if is_ambiguous_mode(raw):
            raise AmbiguousModeError(f"{self._section}.{key}", raw)
        if key in self._table and not accepts_mode(raw):
            self._note(key, reason=REASON_MODE, used=oct(default))
        return mode_of(raw, default)

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
    "REASON_AMBIGUOUS_MODE",
    "REASON_FLAG",
    "REASON_MODE",
    "REASON_PATH",
    "REASON_PERCENT",
    "REASON_POSITIVE_FLOAT",
    "REASON_POSITIVE_INT",
    "REASON_SECTION",
    "USED_PLATFORM_STATE_FILE",
    "AmbiguousModeError",
    "RejectedValue",
    "SectionValues",
    "accepts_flag",
    "accepts_mode",
    "accepts_path",
    "accepts_percent",
    "accepts_positive_float",
    "accepts_positive_int",
    "accepts_tree_density",
    "flag",
    "is_ambiguous_mode",
    "mode_of",
    "path_or_none",
    "percent",
    "positive_float",
    "positive_int",
    "reason_tree_density",
    "rendered",
    "tree_density_of",
]
