"""Where the logging console writes, decided the way lib_log_rich decides it.

lib_log_rich takes its console stream from the ``LOG_CONSOLE_STREAM`` environment
variable when that is set, and from ``[lib_log_rich] console_stream`` otherwise,
lower-cased: its resolver reads ``os.getenv("LOG_CONSOLE_STREAM") or
console_stream``. A command whose standard output IS its product needs that
answer before it writes, because a stream reaching stdout puts log lines inside
the product - ``snapshot -o -`` wrote a capture that ``--replay`` then refused
with "Extra data".

Contents:
    * :class:`ConsoleStreamChoice` - the stream in force, and the setting that chose it
    * :func:`console_stream_in_force` - read it from the configuration and environment
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from lib_layered_config import Config

#: The environment variable lib_log_rich reads AHEAD of its configuration.
ENVIRONMENT_VARIABLE: Final[str] = "LOG_CONSOLE_STREAM"

#: The configuration key, spelled the way ``--set`` and ``lsdsk config`` spell it.
CONFIG_KEY: Final[str] = "lib_log_rich.console_stream"

#: What lib_log_rich writes to when neither names a stream.
_LIBRARY_DEFAULT: Final[str] = "stderr"

#: The streams that write to standard output. ``both`` is one of them.
_REACHING_STANDARD_OUTPUT: Final[frozenset[str]] = frozenset({"stdout", "both"})


class ConsoleStreamChoice(NamedTuple):
    """The console stream in force, and which setting chose it.

    Attributes:
        stream: The stream, lower-cased as the library matches it.
        setting: The name a person changes to move it - the environment
            variable when that decided, the configuration key otherwise.

    Example:
        >>> ConsoleStreamChoice("both", CONFIG_KEY).reaches_standard_output
        True
        >>> ConsoleStreamChoice("stderr", CONFIG_KEY).reaches_standard_output
        False
    """

    stream: str
    setting: str

    @property
    def reaches_standard_output(self) -> bool:
        """Whether log lines land on standard output."""
        return self.stream in _REACHING_STANDARD_OUTPUT


def console_stream_in_force(config: Config) -> ConsoleStreamChoice:
    """The stream lib_log_rich writes its console to for this run.

    Read in the library's own order, so the answer cannot disagree with where
    the lines actually go: an environment variable saying ``stderr`` beats a
    configured ``stdout``, and a check reading the key alone would refuse a run
    whose log never goes near stdout.

    Args:
        config: The merged configuration, ``--set`` overrides included.

    Returns:
        The stream, and the setting that chose it.
    """
    from_environment = os.environ.get(ENVIRONMENT_VARIABLE, "")
    if from_environment:
        return ConsoleStreamChoice(stream=from_environment.strip().lower(), setting=ENVIRONMENT_VARIABLE)
    configured: object = config.get(CONFIG_KEY, default=None)
    stream = configured.strip().lower() if isinstance(configured, str) and configured.strip() else _LIBRARY_DEFAULT
    return ConsoleStreamChoice(stream=stream, setting=CONFIG_KEY)


__all__ = ["CONFIG_KEY", "ENVIRONMENT_VARIABLE", "ConsoleStreamChoice", "console_stream_in_force"]
