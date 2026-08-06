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


__all__ = [
    "ConfigurationError",
    "UnsupportedPlatformError",
]
