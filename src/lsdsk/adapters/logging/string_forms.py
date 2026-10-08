"""The one-line text forms the shipped ``[lib_log_rich]`` comments document for four settings.

An environment variable or ``.env`` line can only carry text, so the shipped
``90-logging.toml`` documents a text form for each setting whose real shape is a
list or a table, and quotes an example of it: ``host:port`` for the Graylog
endpoint, ``max:window`` for the rate limit, ``LEVEL=style,...`` for the console
styles and ``field=regex,...`` for the scrub patterns. The logging library
validates the real shape and refused the text, so every one of those examples
was inert (``Input should be a valid tuple. Using []``) while the comment above
it promised it worked.

The text is turned into the real shape here, before the library sees it, with
the grammar the library already uses for its own ``LOG_*`` variables where it
exports one. Text that is not in the documented form is left exactly as it was,
so the library refuses it with the usual one-line warning and the fallback.

Contents:
    * :func:`with_documented_forms` - a ``[lib_log_rich]`` table with those texts converted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from lib_log_rich.runtime.settings import parse_console_styles, parse_scrub_patterns

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

#: The highest TCP/UDP port number.
_LAST_PORT: Final = 65535


def _endpoint(text: str) -> list[object] | None:
    """``host:port`` or ``[v6-address]:port`` as ``[host, port]``, or ``None`` when it is neither.

    An address with colons of its own must be bracketed, as it is in a URL, or the
    split between address and port would be a guess.

    Example:
        >>> _endpoint("graylog.example.com:12201"), _endpoint("[::1]:12201")
        (['graylog.example.com', 12201], ['::1', 12201])
        >>> _endpoint("::1:12201") is None and _endpoint("host:port") is None and _endpoint("host") is None
        True
    """
    host, separator, port = text.strip().rpartition(":")
    bracketed = host.startswith("[") and host.endswith("]")
    host = host[1:-1] if bracketed else host
    plain = bool(host) and (bracketed or ":" not in host)
    if not (separator and plain and port.isascii() and port.isdigit() and 0 < int(port) <= _LAST_PORT):
        return None
    return [host, int(port)]


def _rate_limit(text: str) -> list[object] | None:
    """``max:window_seconds`` as ``[max, window]``, or ``None`` when it is not that form.

    Example:
        >>> _rate_limit("100:60"), _rate_limit("100"), _rate_limit("0:60")
        ([100, 60.0], None, None)
    """
    maximum, separator, window = text.strip().partition(":")
    try:
        events, seconds = int(maximum), float(window)
    except ValueError:
        return None
    return [events, seconds] if separator and events > 0 and 0 < seconds < float("inf") else None


#: How each setting's text form is read; the answer ``None`` means "not that form".
_FORMS: Final[Mapping[str, Callable[[str], object | None]]] = {
    "graylog_endpoint": _endpoint,
    "rate_limit": _rate_limit,
    "console_styles": parse_console_styles,
    "scrub_patterns": parse_scrub_patterns,
}


def with_documented_forms(section: Mapping[str, object]) -> dict[str, object]:
    """`section` with each documented text form turned into the shape the library validates.

    Args:
        section: The ``[lib_log_rich]`` table as the layers merged it.

    Returns:
        A new table. A value that is not text, or is text not in its setting's
        documented form, is passed through untouched.

    Example:
        >>> with_documented_forms({"graylog_endpoint": "h:1", "service": "x", "rate_limit": []})
        {'graylog_endpoint': ['h', 1], 'service': 'x', 'rate_limit': []}
    """
    converted = dict(section)
    for key, read in _FORMS.items():
        value = section.get(key)
        if isinstance(value, str) and (shaped := read(value)) is not None:
            converted[key] = shaped
    return converted


__all__ = ["with_documented_forms"]
