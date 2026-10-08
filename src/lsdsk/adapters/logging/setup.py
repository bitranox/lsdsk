"""Centralized logging initialization for all entry points.

Provides a single source of truth for lib_log_rich runtime configuration,
eliminating duplication between module entry (__main__.py) and console script
(cli.py) while ensuring initialization happens exactly once.

Contents:
    * :func:`init_logging` - idempotent logging initialization with layered config.
    * :func:`run_log_demo` - one line per severity, on the same guarded console.
    * :func:`_build_runtime_config` - constructs RuntimeConfig from a ``[lib_log_rich]`` table.
    * :func:`_start_runtime` - starts it, falling back on a value the library refuses.

System Role:
    Lives in the adapters/platform layer. All entry points (module execution,
    console scripts, tests) delegate to this module for logging setup, ensuring
    consistent runtime behavior across invocation paths.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import lib_log_rich.config
import lib_log_rich.runtime
from lib_log_rich.domain import ConsoleStream, LogLevel
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES
from lib_log_rich.runtime import RichConsoleAdapter
from pydantic import BaseModel, ConfigDict

from lsdsk import __init__conf__

from ...domain.text import visible_text

# A sibling adapter, not a layer breach: safe_console owns this project's answer to
# a stream whose reader has gone, and that answer has to be the same one wherever
# the writing happens.
from ..cli import safe_console
from ..config.loader import shipped_section
from ..config.values import RejectedValue, rendered
from .refusals import (
    REFUSALS,
    changed_keys,
    check_timeouts,
    ignored_sentence,
    offending_keys,
    offending_variables,
    variable_sentence,
    with_shipped_values,
)
from .string_forms import with_documented_forms

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
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


def _build_runtime_config(section: Mapping[str, object]) -> lib_log_rich.runtime.RuntimeConfig:
    """Build RuntimeConfig from a ``[lib_log_rich]`` table.

    Centralizes the mapping from lib_layered_config to lib_log_rich
    RuntimeConfig. Uses Pydantic for single-parse validation at the boundary.

    Args:
        section: The ``[lib_log_rich]`` table, as the layers merged it or as the
            package ships it.

    Returns:
        Fully configured runtime settings ready for lib_log_rich.init().

    Raises:
        pydantic.ValidationError: When ``service`` or ``environment`` is not text,
            or the library's own model refuses a value.

    Note:
        All parameters documented in defaultconfig.toml can be specified.
        Unspecified values use lib_log_rich's built-in defaults. The service and
        environment parameters default to package metadata when not configured.
    """
    parsed = LoggingConfigModel.model_validate(dict(section))

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
#:
#: Each RECORDS a stdout failure rather than raising it: the library writes to its
#: console inside ``except Exception``, so a refusal raised there was swallowed and
#: the run left 0 with its output lost (``LOG_CONSOLE_STREAM=stdout lsdsk logdemo >
#: /dev/full``). The final flush answers the record instead.
_GUARDED_WRITERS: Final[Mapping[ConsoleStream, Callable[[], IO[str]]]] = {
    ConsoleStream.STDOUT: partial(safe_console.safe_stream, records_failures=True),
    ConsoleStream.STDERR: partial(safe_console.safe_stream, err=True, records_failures=True),
    ConsoleStream.BOTH: partial(safe_console.safe_stream_to_both, records_failures=True),
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
    stream = appearance.stream
    target = cast("IO[str] | None", appearance.stream_target)
    guarded = _GUARDED_WRITERS.get(stream)
    if guarded is not None:
        target = guarded()
        stream = ConsoleStream.CUSTOM
    return RichConsoleAdapter(
        force_color=appearance.force_color,
        no_color=appearance.no_color,
        styles=appearance.styles,
        format_preset=appearance.format_preset,
        format_template=appearance.format_template,
        # The adapter's parameter is text, so the member crosses as its value here
        # and nowhere earlier.
        stream=stream.value,
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
    configured, notes = _configured_section(config)
    notes.extend(_start_runtime(configured, shipped_section(_SECTION)))
    for note in notes:
        safe_console.echo(note, err=True)
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.attach_std_logging()


#: The section lib_log_rich's settings live under.
_SECTION: Final = "lib_log_rich"


def _configured_section(config: Config) -> tuple[dict[str, object], list[str]]:
    """The ``[lib_log_rich]`` table to start from, and a warning if it is not a table.

    Args:
        config: The merged configuration.

    Returns:
        The table, or the shipped one in place of a value that is not a table,
        with the warning saying so.
    """
    raw: object = config.get(_SECTION, default={})
    if isinstance(raw, Mapping):
        return with_documented_forms(cast("Mapping[str, object]", raw)), []
    note = RejectedValue(
        dotted=_SECTION, raw=visible_text(rendered(raw)), reason="not a table", used="the shipped logging settings"
    )
    return shipped_section(_SECTION), [note.as_sentence()]


def _refused_by(section: Mapping[str, object]) -> BaseException | None:
    """Start the logging runtime from `section`, answering what refused it if anything did.

    Args:
        section: The ``[lib_log_rich]`` table to start from.

    Returns:
        ``None`` once the runtime is running, or what the library raised.
    """
    try:
        check_timeouts(section, os.environ)
        lib_log_rich.runtime.init(_build_runtime_config(section))
    except REFUSALS as refused:
        return refused
    return None


@contextmanager
def _set_aside(names: Sequence[str]) -> Generator[None]:
    """Take `names` out of the environment for the duration, and put them back.

    Args:
        names: Environment variables to hide.

    Yields:
        Nothing; the variables are absent inside the block.
    """
    kept = {name: os.environ.pop(name) for name in names if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(kept)


def _start_runtime(configured: Mapping[str, object], shipped: Mapping[str, object]) -> list[str]:
    """Start the runtime, falling back to the shipped settings for any value it refused.

    Logging is set up before any command runs, so a value the library refused
    used to end every command - the diagnosis included - with the library's own
    exception and no envelope. The rule the rest of the configuration follows
    applies here too: a value the tool cannot use falls back to the shipped one
    and says so.

    The attempts after the first narrow what is trusted: the refused settings put
    back to their shipped values; then the ``LOG_*`` variables the refusal is
    about set aside for the start, because lib_log_rich reads those ahead of
    every setting and no fallback in this tool's configuration can outrank one;
    then both, with the shipped table whole. Every command binds a logging
    context, so running on with no runtime at all is not an option. If the
    shipped settings with those variables set aside are refused too, that is a
    fault in this tool, and the original refusal is raised.

    Args:
        configured: The ``[lib_log_rich]`` table as the layers merged it.
        shipped: The same table as the package ships it.

    Returns:
        The warnings to print, empty when the settings were used as given.

    Raises:
        ValueError: The library's refusal, when nothing above could repair it.
        TypeError: Likewise.
        OverflowError: Likewise, for a value too large for a C-sized call.
    """
    refused = _refused_by(configured)
    if refused is None:
        return []
    offenders = offending_keys(refused, configured, shipped)
    variables = offending_variables(refused, os.environ)
    attempts: tuple[tuple[Mapping[str, object], Sequence[str], Sequence[str]], ...] = (
        (with_shipped_values(configured, shipped, offenders), (), offenders),
        (configured, variables, ()),
        (shipped, variables, changed_keys(configured, shipped)),
    )
    for section, hidden, keys in attempts:
        # Read before the variables are hidden: the sentence quotes their values.
        notes = [variable_sentence(name, refused=refused, environ=os.environ) for name in hidden]
        with _set_aside(hidden):
            if _refused_by(section) is None:
                return [
                    *(ignored_sentence(key, refused=refused, configured=configured, shipped=shipped) for key in keys),
                    *notes,
                ]
    raise refused


#: The preview's lines, one per severity, worded as lib_log_rich's own demo words them.
_DEMO_LINES: Final[tuple[tuple[LogLevel, str], ...]] = (
    (LogLevel.DEBUG, "Debug message"),
    (LogLevel.INFO, "Information message"),
    (LogLevel.WARNING, "Warning message"),
    (LogLevel.ERROR, "Error message"),
    (LogLevel.CRITICAL, "Critical message"),
)


def run_log_demo(theme: str) -> str:
    """Log one line per severity in `theme`, through the console every run logs through.

    ``lib_log_rich.logdemo()`` would do the same with a runtime it builds
    itself, and that runtime has no ``console_adapter_factory``: rich writes
    straight to the raw stream, so its ``on_broken_pipe`` was back in charge for
    the one command that exists to preview logging. Measured through
    ``lsdsk logdemo`` with stderr's reader gone and stdout read: 141 and 0 bytes
    of stdout, where every other command's log line costs that line alone. So
    the runtime is built here, with the settings the library's demo uses and
    :func:`_guarded_console` as its console. ``LOG_CONSOLE_STREAM`` still
    applies, as it does to the library's demo.

    The caller must have shut the run's own runtime down: one runtime at a time
    is the library's rule.

    Args:
        theme: A name from ``lib_log_rich.domain.palettes.CONSOLE_STYLE_THEMES``.

    Returns:
        The theme's key as the library spells it.

    Raises:
        KeyError: When `theme` names no palette the library publishes. The
            command line refuses one before this is reached.

    Side Effects:
        Initialises the logging runtime, writes five lines to the console and
        shuts the runtime down again.
    """
    key = theme.strip().lower()
    styles = dict(CONSOLE_STYLE_THEMES[key])
    lib_log_rich.runtime.init(
        lib_log_rich.runtime.RuntimeConfig(
            service="logdemo",
            environment=f"demo-{key}",
            console_level=LogLevel.DEBUG,
            backend_level=LogLevel.CRITICAL,
            enable_ring_buffer=False,
            # On the calling thread, as the library's demo runs it: the preview
            # is over when this returns, with nothing left in a queue.
            queue_enabled=False,
            force_color=True,
            console_styles=styles,
            console_theme=key,
            console_adapter_factory=_guarded_console,
        )
    )
    try:
        with lib_log_rich.runtime.bind(job_id=f"logdemo-{key}", request_id="demo"):
            logger = lib_log_rich.runtime.getLogger("logdemo")
            for level, message in _DEMO_LINES:
                logger.log(level, "[%s] %s", key, message, extra={"theme": key, "level": level.severity})
    finally:
        lib_log_rich.runtime.shutdown()
    return key


__all__ = [
    "LoggingConfigModel",
    "init_logging",
    "run_log_demo",
]
