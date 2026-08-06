"""Click context helpers for CLI state management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import lib_cli_exit_tools

if TYPE_CHECKING:
    from pathlib import Path

    import rich_click as click
    from lib_layered_config import Config

    from lsdsk.composition import AppServices


class TracebackState(NamedTuple):
    """Captured traceback configuration.

    Two booleans held positionally are the one shape a type checker cannot
    protect: a swapped pair is the same type in both slots, so it passes every
    check and turns colour on instead of tracebacks. Naming them makes the swap
    unwritable and removes the ``state[0]`` the reader had to decode.

    Attributes:
        traceback_enabled: Whether full tracebacks are printed.
        force_color: Whether colour is forced in traceback output.

    Example:
        >>> TracebackState(True, False).traceback_enabled
        True
    """

    traceback_enabled: bool
    force_color: bool


@dataclass(slots=True)
class CLIContext:
    """Typed CLI context for Click subcommand access."""

    traceback: bool
    config: Config
    services: AppServices
    profile: str | None = None
    set_overrides: tuple[str, ...] = ()
    replay: Path | None = None
    history_file: Path | None = None
    no_record: bool = False


def store_cli_context(ctx: click.Context, context: CLIContext) -> None:
    """Store CLI state on the Click context for subcommands to read.

    Takes the built context rather than its nine fields. Every one of those was
    a pure tramp - forwarded, never read - into a body whose only statement
    constructed this object, so the parameter list grew by one each time a
    global option was added and the signature said nothing the dataclass did not
    already say.

    Args:
        ctx: Click context associated with the current invocation.
        context: The state subcommands will read back.

    Example:
        >>> from unittest.mock import MagicMock
        >>> from lsdsk.composition import build_production
        >>> ctx = MagicMock()
        >>> state = CLIContext(traceback=True, config=MagicMock(), services=build_production())
        >>> store_cli_context(ctx, state)
        >>> ctx.obj.traceback
        True
    """
    ctx.obj = context


def get_cli_context(ctx: click.Context) -> CLIContext:
    """Retrieve typed CLI state from Click context.

    Args:
        ctx: Click context containing CLI state.

    Returns:
        CLIContext dataclass with typed access to CLI state.

    Raises:
        RuntimeError: If CLI context was not properly initialized.

    Example:
        >>> from unittest.mock import MagicMock
        >>> ctx = MagicMock()
        >>> mock_config = MagicMock()
        >>> mock_services = MagicMock()
        >>> ctx.obj = CLIContext(traceback=False, config=mock_config, services=mock_services)
        >>> cli_ctx = get_cli_context(ctx)
        >>> cli_ctx.traceback
        False
    """
    if not isinstance(ctx.obj, CLIContext):
        raise RuntimeError("CLI context not initialized. Call store_cli_context first.")
    return ctx.obj


def apply_traceback_preferences(enabled: bool) -> None:
    """Synchronise shared traceback flags with the requested preference.

    Args:
        enabled: ``True`` enables full tracebacks with colour.

    Example:
        >>> apply_traceback_preferences(True)
        >>> bool(lib_cli_exit_tools.config.traceback)
        True
    """
    lib_cli_exit_tools.config.traceback = bool(enabled)
    lib_cli_exit_tools.config.traceback_force_color = bool(enabled)


def snapshot_traceback_state() -> TracebackState:
    """Capture the current traceback configuration for later restoration.

    Returns:
        The current traceback configuration.

    Example:
        >>> snapshot_traceback_state().force_color in (True, False)
        True
    """
    return TracebackState(
        bool(getattr(lib_cli_exit_tools.config, "traceback", False)),
        bool(getattr(lib_cli_exit_tools.config, "traceback_force_color", False)),
    )


def restore_traceback_state(state: TracebackState) -> None:
    """Reapply a previously captured traceback configuration.

    Args:
        state: What :func:`snapshot_traceback_state` captured.

    Example:
        >>> original = snapshot_traceback_state()
        >>> apply_traceback_preferences(True)
        >>> restore_traceback_state(original)
        >>> lib_cli_exit_tools.config.traceback == original.traceback_enabled
        True
    """
    lib_cli_exit_tools.config.traceback = state.traceback_enabled
    lib_cli_exit_tools.config.traceback_force_color = state.force_color


__all__ = [
    "CLIContext",
    "TracebackState",
    "apply_traceback_preferences",
    "get_cli_context",
    "restore_traceback_state",
    "snapshot_traceback_state",
    "store_cli_context",
]
