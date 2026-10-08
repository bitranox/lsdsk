"""Which ``[lib_log_rich]`` setting the logging library refused, in this tool's words.

lib_log_rich judges its settings in three places - its ``RuntimeConfig`` model,
its settings resolver and the composition that parses each level - and only the
first names a field. So a refusal is attributed here from what the library DID
say: the field a validation error names where there is one, and otherwise every
setting this run changed from the shipped value whose key or value the message
quotes. When nothing is quoted, every changed setting is named, which says more
than is wrong rather than pointing at the wrong one.

Contents:
    * :data:`REFUSALS` - what the library raises for a value it cannot use.
    * :func:`changed_keys` - the settings this run holds differently from the shipped ones.
    * :func:`offending_keys` - the settings a refusal is about.
    * :func:`with_shipped_values` - a section with those settings put back.
    * :func:`ignored_sentence` - the warning for one setting that fell back.
    * :func:`offending_variables` - the ``LOG_*`` variables a refusal is about.
    * :func:`variable_sentence` - the warning for one such variable set aside.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from ...domain.text import visible_text
from ..config.values import RejectedValue, rendered

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

#: The library raises ``ValueError`` (pydantic's ``ValidationError`` is one) for a
#: value it cannot use, ``TypeError`` where a value has the wrong shape for a
#: call it makes with it, and ``OverflowError`` where a value is the right shape
#: but too large for a C-sized call: ``ring_buffer_size`` is handed straight to
#: ``collections.deque(maxlen=...)``, whose ``maxlen`` is a C ``Py_ssize_t``, so
#: an int past that range raises ``OverflowError`` rather than ``ValueError`` -
#: measured with ``ring_buffer_size`` set to ``10**27``, which otherwise escaped
#: as a bare ``OverflowError: Python int too large to convert to C ssize_t`` with
#: no envelope. Anything else is a fault in this tool or the library and is left
#: to reach the handler that honours ``--traceback``.
REFUSALS: Final = (ValueError, TypeError, OverflowError)

#: How the library prefixes a refusal from its settings resolver; it says nothing
#: a reader of the warning needs.
_RESOLVER_PREFIX: Final = "Invalid runtime settings: "

#: What a setting the package does not ship falls back to.
_LIBRARY_DEFAULT: Final = "lib_log_rich's own default"

#: The prefix of every environment variable lib_log_rich reads by itself.
_LIBRARY_VARIABLE_PREFIX: Final = "LOG_"


def changed_keys(configured: Mapping[str, object], shipped: Mapping[str, object]) -> list[str]:
    """The settings this run holds differently from the shipped ones.

    Args:
        configured: The ``[lib_log_rich]`` table as the layers merged it.
        shipped: The same table as the package ships it.

    Returns:
        Each key that is absent from `shipped` or holds a different value.

    Example:
        >>> changed_keys({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 9})
        ['b', 'c']
    """
    return [key for key, value in configured.items() if key not in shipped or shipped[key] != value]


def _validation_errors(refused: BaseException) -> Iterator[ValidationError]:
    """Every pydantic report in `refused` and the exceptions it was raised from."""
    seen: BaseException | None = refused
    while seen is not None:
        if isinstance(seen, ValidationError):
            yield seen
        seen = seen.__cause__ or seen.__context__


def _named_fields(refused: BaseException) -> dict[str, str]:
    """The top-level field each pydantic problem names, with its reason.

    Example:
        >>> from pydantic import BaseModel
        >>> class Thing(BaseModel):
        ...     size: int
        >>> try:
        ...     Thing(size="big")
        ... except ValidationError as error:
        ...     _named_fields(error)
        {'size': 'Input should be a valid integer, unable to parse string as an integer'}
    """
    named: dict[str, str] = {}
    for error in _validation_errors(refused):
        for problem in error.errors(include_url=False, include_context=False, include_input=False):
            location = problem["loc"]
            if location:
                named.setdefault(str(location[0]), problem["msg"])
    return named


def _message(refused: BaseException) -> str:
    """The library's own sentence, first line only and without the resolver's prefix.

    Example:
        >>> _message(ValueError("Invalid runtime settings: ring_buffer_size must be positive"))
        'ring_buffer_size must be positive'
    """
    text = str(refused).strip().splitlines()[0] if str(refused).strip() else type(refused).__name__
    return text.removeprefix(_RESOLVER_PREFIX)


def _quoted_in(message: str, key: str, value: object) -> bool:
    """Whether `message` names the setting, by its key or by the text it holds."""
    return key in message or (isinstance(value, str) and repr(value) in message)


def offending_keys(
    refused: BaseException, configured: Mapping[str, object], shipped: Mapping[str, object]
) -> list[str]:
    """The settings a refusal is about, as precisely as the library's words allow.

    Args:
        refused: What starting the runtime raised.
        configured: The ``[lib_log_rich]`` table that was refused.
        shipped: The same table as the package ships it.

    Returns:
        The keys to fall back on. Never empty when `configured` differs from
        `shipped`, because a refusal nothing can be attributed to is put on every
        changed setting.

    Examples:
        >>> offending_keys(ValueError("Unknown log level: 'WARN'"), {"level": "WARN", "x": 1}, {"level": "INFO"})
        ['level']
        >>> offending_keys(ValueError("something odd"), {"a": 1, "b": 2}, {"a": 0})
        ['a', 'b']
    """
    changed = changed_keys(configured, shipped)
    named = [key for key in _named_fields(refused) if key in configured]
    if named:
        return named
    message = _message(refused)
    quoted = [key for key in changed if _quoted_in(message, key, configured[key])]
    return quoted or changed


def with_shipped_values(
    configured: Mapping[str, object], shipped: Mapping[str, object], keys: Sequence[str]
) -> dict[str, object]:
    """`configured` with each of `keys` put back to its shipped value.

    A key the package does not ship is dropped instead, so the library's own
    default applies to it.

    Args:
        configured: The ``[lib_log_rich]`` table that was refused.
        shipped: The same table as the package ships it.
        keys: The settings to put back.

    Returns:
        A new table; `configured` is left as it was.

    Example:
        >>> sorted(with_shipped_values({"a": 1, "b": 2, "c": 3}, {"a": 0}, ["a", "c"]).items())
        [('a', 0), ('b', 2)]
    """
    kept = {key: value for key, value in configured.items() if key not in keys}
    kept.update({key: shipped[key] for key in keys if key in shipped})
    return kept


def ignored_sentence(
    key: str, *, refused: BaseException, configured: Mapping[str, object], shipped: Mapping[str, object]
) -> str:
    """The warning for one setting that fell back, worded as every other fallback's is.

    Args:
        key: The setting, under ``[lib_log_rich]``.
        refused: What the library raised, which carries the reason.
        configured: The table holding the refused value.
        shipped: The table holding the value used instead.

    Returns:
        One ``Warning: ignoring ...`` sentence.

    Example:
        >>> ignored_sentence(
        ...     "console_level",
        ...     refused=ValueError("Unknown log level: 'WARN'"),
        ...     configured={"console_level": "WARN"},
        ...     shipped={"console_level": "INFO"},
        ... )
        "Warning: ignoring lib_log_rich.console_level=WARN: Unknown log level: 'WARN'. Using INFO."
    """
    reason = _named_fields(refused).get(key) or _message(refused)
    used = rendered(shipped[key]) if key in shipped else _LIBRARY_DEFAULT
    return RejectedValue(
        dotted=f"lib_log_rich.{visible_text(key)}",
        raw=visible_text(rendered(configured.get(key))),
        reason=visible_text(reason.rstrip(".")),
        used=used,
    ).as_sentence()


def offending_variables(refused: BaseException, environ: Mapping[str, str]) -> list[str]:
    """The ``LOG_*`` variables a refusal is about.

    lib_log_rich reads these itself, ahead of every setting - the shipped ones
    included - so no fallback inside this tool's configuration can repair one.

    Args:
        refused: What starting the runtime raised.
        environ: The process environment.

    Returns:
        The variables whose name or value the refusal quotes, or every ``LOG_*``
        variable set when it quotes none of them.

    Example:
        >>> offending_variables(ValueError("Unknown log level: 'NOPE'"), {"LOG_A": "NOPE", "LOG_B": "x", "PATH": "/"})
        ['LOG_A']
    """
    present = {name: value for name, value in environ.items() if name.startswith(_LIBRARY_VARIABLE_PREFIX)}
    message = _message(refused)
    quoted = [name for name, value in present.items() if _quoted_in(message, name, value)]
    return quoted or list(present)


def variable_sentence(name: str, *, refused: BaseException, environ: Mapping[str, str]) -> str:
    """The warning for a ``LOG_*`` variable this run set aside.

    Args:
        name: The variable.
        refused: What the library raised, which carries the reason.
        environ: The environment holding the variable's value.

    Returns:
        One ``Warning: ignoring ...`` sentence.

    Example:
        >>> variable_sentence("LOG_CONSOLE_LEVEL", refused=ValueError("Unknown log level: 'NOPE'"),
        ...     environ={"LOG_CONSOLE_LEVEL": "NOPE"})
        "Warning: ignoring LOG_CONSOLE_LEVEL=NOPE: Unknown log level: 'NOPE'. Using the [lib_log_rich] settings."
    """
    return RejectedValue(
        dotted=visible_text(name),
        raw=visible_text(environ.get(name, "")),
        reason=visible_text(_message(refused).rstrip(".")),
        used="the [lib_log_rich] settings",
    ).as_sentence()


__all__ = [
    "REFUSALS",
    "changed_keys",
    "ignored_sentence",
    "offending_keys",
    "offending_variables",
    "variable_sentence",
    "with_shipped_values",
]
