"""Domain-specific exceptions for typed error handling at boundaries."""

from __future__ import annotations


class ConfigurationError(Exception):
    """Missing, invalid, or incomplete configuration.

    Also raised when a snapshot cannot be read or came from a platform this
    version does not understand, because both are cases of being handed
    something that cannot be worked with rather than a hardware fault.

    Example:
        >>> from lsdsk.domain.errors import ConfigurationError
        >>> err = ConfigurationError("Unknown snapshot schema")
        >>> str(err)
        'Unknown snapshot schema'
    """


class UnsupportedPlatformError(ConfigurationError):
    """This operating system has no hardware reader.

    Rendering a snapshot captured elsewhere still works everywhere, so this is
    raised only when asked to read local hardware.

    A subclass rather than a sibling, so that the one place which has to catch
    everything lsdsk raises can keep spelling it ``ConfigurationError`` and a
    caller who wants to tell "wrong operating system" from "unreadable file"
    can ask for this instead. There is no separate device-read error: a drive
    that refuses an ioctl is recorded against that drive and the scan carries
    on, because one silent device must not cost the reading of every other.

    Example:
        >>> from lsdsk.domain.errors import ConfigurationError, UnsupportedPlatformError
        >>> issubclass(UnsupportedPlatformError, ConfigurationError)
        True
    """


class MissingFileError(ConfigurationError):
    """The file is not there at all.

    A file that is absent and a file that is present and refuses to be read are
    different answers, and only the reader knows which it met: ``Path.exists``
    swallows the OSError and answers ``False`` to both. Callers that must tell
    them apart ask for this - the history store, where an absent file is an
    ordinary first run and an unreadable one means every verdict about to be
    given was computed from nothing.

    A subclass for the same reason :class:`UnsupportedPlatformError` is one: a
    caller that only wants "this file is no good" keeps spelling it
    ``ConfigurationError`` and is unaffected.

    Example:
        >>> from lsdsk.domain.errors import ConfigurationError, MissingFileError
        >>> issubclass(MissingFileError, ConfigurationError)
        True
    """


__all__ = [
    "ConfigurationError",
    "MissingFileError",
    "UnsupportedPlatformError",
]
