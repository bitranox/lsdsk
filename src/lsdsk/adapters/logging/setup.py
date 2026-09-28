"""Centralized logging initialization for all entry points.

Provides a single source of truth for lib_log_rich runtime configuration,
eliminating duplication between module entry (__main__.py) and console script
(cli.py) while ensuring initialization happens exactly once.

Contents:
    * :func:`init_logging` - idempotent logging initialization with layered config.
    * :func:`_build_runtime_config` - constructs RuntimeConfig from layered sources.

System Role:
    Lives in the adapters/platform layer. All entry points (module execution,
    console scripts, tests) delegate to this module for logging setup, ensuring
    consistent runtime behavior across invocation paths.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final, cast

import lib_log_rich.config
import lib_log_rich.runtime
from lib_log_rich.runtime import RichConsoleAdapter
from pydantic import BaseModel, ConfigDict

from lsdsk import __init__conf__

# A sibling adapter, not a layer breach: safe_console owns this project's answer to
# a stream whose reader has gone, and that answer has to be the same one wherever
# the writing happens.
from ..cli import safe_console

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import IO

    from lib_layered_config import Config
    from lib_log_rich.runtime import ConsoleAppearance


class LoggingConfigModel(BaseModel):
    """Pydantic model for [lib_log_rich] config section validation.

    Used at the boundary to parse configuration dictionaries into typed fields.
    Extra fields are allowed to pass through to lib_log_rich.RuntimeConfig.

    Example:
        >>> model = LoggingConfigModel(service="myapp", environment="staging")
        >>> model.service
        'myapp'
        >>> model.environment
        'staging'

        >>> default = LoggingConfigModel()
        >>> default.environment
        'prod'
    """

    service: str | None = None
    environment: str = "prod"

    model_config = ConfigDict(extra="allow")


def _build_runtime_config(config: Config) -> lib_log_rich.runtime.RuntimeConfig:
    """Build RuntimeConfig from a Config object.

    Centralizes the mapping from lib_layered_config to lib_log_rich
    RuntimeConfig. Uses Pydantic for single-parse validation at the boundary.

    Args:
        config: Already-loaded layered configuration object.

    Returns:
        Fully configured runtime settings ready for lib_log_rich.init().

    Note:
        Configuration is read from the [lib_log_rich] section. All parameters
        documented in defaultconfig.toml can be specified. Unspecified values
        use lib_log_rich's built-in defaults. The service and environment
        parameters default to package metadata when not configured.
    """
    log_raw: object = config.get("lib_log_rich", default={})
    parsed = LoggingConfigModel.model_validate(cast("dict[str, object]", log_raw) if log_raw else {})

    # Apply defaults for required fields
    service = parsed.service or __init__conf__.name
    environment = parsed.environment

    # Get extra fields passed through by Pydantic
    extra_config = parsed.model_dump(exclude={"service", "environment"}, exclude_none=True)

    return lib_log_rich.runtime.RuntimeConfig(
        service=service,
        environment=environment,
        console_adapter_factory=_guarded_console,
        **extra_config,
    )


#: The guarded writer for each console stream a setting can name.
#:
#: ``custom`` and ``none`` are left alone: the first names a target this does not
#: own, and the second writes nowhere. ``both`` is here, not with them: it is a
#: plain setting like the other two, and left to the library it becomes a tee over
#: the RAW streams, so a departed stderr reader sent stdout to the null device.
_GUARDED_WRITERS: Final[Mapping[str, Callable[[], IO[str]]]] = {
    "stdout": safe_console.safe_stream,
    "stderr": partial(safe_console.safe_stream, err=True),
    "both": safe_console.safe_stream_to_both,
}


def _guarded_console(appearance: ConsoleAppearance) -> RichConsoleAdapter:
    """Build lib_log_rich's console on a writer that answers a departed reader correctly.

    lib_log_rich renders through rich, and rich's ``Console.on_broken_pipe`` runs
    ``os.dup2(devnull, sys.stdout.fileno())`` - hardcoded to STDOUT whichever
    stream actually broke - then raises ``SystemExit(1)``, this tool's code for an
    actionable finding. Measured on ``lsdsk config`` with stderr's reader gone and
    stdout read normally: the report went from 13,166 bytes to 1, with nothing on
    any stream to say the rest had been discarded.

    Decided HERE, from the appearance the library hands its console factory,
    rather than by rewriting ``console_stream`` in the settings: the library reads
    ``LOG_CONSOLE_STREAM`` ahead of the configuration, so a ``custom`` route put in
    the settings was overridden by that variable and rich was back on the raw
    stream. The appearance is resolved after the variable, so this sees the stream
    the lines really go to, whichever setting chose it. ``console_adapter_factory``
    is the library's own seam for this, so nothing third-party is patched: rich is
    handed :func:`~lsdsk.adapters.cli.safe_console.safe_stream` (or, for ``both``,
    :func:`~lsdsk.adapters.cli.safe_console.safe_stream_to_both`), whose writes
    never let a ``BrokenPipeError`` reach rich's handler.

    Args:
        appearance: The console settings as the library resolved them.

    Returns:
        The console adapter the library would have built, on the guarded writer
        where the stream is one this can guard.
    """
    stream = str(appearance.stream.value)
    target = cast("IO[str] | None", appearance.stream_target)
    guarded = _GUARDED_WRITERS.get(stream)
    if guarded is not None:
        target = guarded()
        stream = "custom"
    return RichConsoleAdapter(
        force_color=appearance.force_color,
        no_color=appearance.no_color,
        styles=appearance.styles,
        format_preset=appearance.format_preset,
        format_template=appearance.format_template,
        stream=stream,
        stream_target=target,
    )


def init_logging(config: Config) -> None:
    """Initialize lib_log_rich runtime with the provided configuration.

    All entry points need logging configured, but the runtime should only
    be initialized once regardless of how many times this function is called.
    Loads .env files (to make LOG_* variables available), checks if lib_log_rich
    is already initialized, and configures it with settings from the provided
    Config object. Bridges standard Python logging to lib_log_rich for domain
    code compatibility.

    Args:
        config: Already-loaded layered configuration object containing logging
            settings in the [lib_log_rich] section.

    Side Effects:
        Loads .env files into the process environment on first invocation.
        May initialize the global lib_log_rich runtime on first invocation.
        Subsequent calls have no effect.

    Note:
        This function is safe to call multiple times. The first call loads .env
        and initializes the runtime; subsequent calls check the initialization
        state and return immediately if already initialized.

        The .env loading enables lib_log_rich to read LOG_* environment variables
        from .env files in the current directory or parent directories. This
        provides the highest precedence override mechanism for logging configuration.

    Example:
        >>> from lib_layered_config import Config
        >>> config = Config({"lib_log_rich": {"environment": "test"}}, {})
        >>> init_logging(config)  # doctest: +SKIP
    """
    if lib_log_rich.runtime.is_initialised():
        return
    lib_log_rich.config.enable_dotenv()
    runtime_config = _build_runtime_config(config)
    lib_log_rich.runtime.init(runtime_config)
    lib_log_rich.runtime.attach_std_logging()


__all__ = [
    "LoggingConfigModel",
    "init_logging",
]
