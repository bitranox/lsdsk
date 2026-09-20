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

import sys
from typing import TYPE_CHECKING, Any, Final, cast

import lib_log_rich.config
import lib_log_rich.runtime
from pydantic import BaseModel, ConfigDict

from lsdsk import __init__conf__

# A sibling adapter, not a layer breach: safe_console owns this project's answer to
# a stream whose reader has gone, and that answer has to be the same one wherever
# the writing happens.
from ..cli import safe_console

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lib_layered_config import Config


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
        **_routed_through_the_guarded_stream(extra_config),
    )


#: The console streams this can hand lib_log_rich a guarded writer for.
#:
#: ``both``, ``custom`` and ``none`` are left alone: the first two already name a
#: target this does not own, and the third writes nowhere.
_GUARDABLE_CONSOLE_STREAMS: Final[frozenset[str]] = frozenset({"stdout", "stderr"})


def _routed_through_the_guarded_stream(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Point lib_log_rich's console at a writer that answers a departed reader correctly.

    lib_log_rich renders through rich, and rich's ``Console.on_broken_pipe`` runs
    ``os.dup2(devnull, sys.stdout.fileno())`` - hardcoded to STDOUT whichever
    stream actually broke - then raises ``SystemExit(1)``, this tool's code for an
    actionable finding. Measured on ``lsdsk config`` with stderr's reader gone and
    stdout read normally: the report went from 13,166 bytes to 1, with nothing on
    any stream to say the rest had been discarded.

    ``console_stream="custom"`` with a ``console_stream_target`` is the library's
    own seam for this, so nothing third-party is patched: rich is handed
    :func:`~lsdsk.adapters.cli.safe_console.safe_stream`, whose writes never let a
    ``BrokenPipeError`` reach rich's handler.

    Args:
        settings: The ``[lib_log_rich]`` values, minus service and environment.
            Typed as the model's own ``Any`` values rather than narrowed to
            ``object``: the model declares ``extra="allow"``, so these are the
            library's keyword arguments passing through, and narrowing them here
            erases every one of their types at the call that spreads them.

    Returns:
        Those settings, with a guarded target substituted where one applies. The
        input is not modified.
    """
    configured = settings.get("console_stream")
    # Lower-cased because lib_log_rich matches it that way, so "STDERR" names the
    # same stream and must not slip past this guard by its spelling.
    stream = configured.lower() if isinstance(configured, str) else ""
    if stream not in _GUARDABLE_CONSOLE_STREAMS:
        return dict(settings)
    return {
        **settings,
        "console_stream": "custom",
        "console_stream_target": safe_console.safe_stream(sys.stderr if stream == "stderr" else sys.stdout),
    }


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
