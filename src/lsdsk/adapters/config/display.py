"""Display configuration - delegates to lib_layered_config.

Thin wrapper around lib_layered_config's Rich-styled display_config,
adding log flush before output to prevent mixing log messages with
configuration display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import lib_log_rich.runtime
from lib_layered_config import Config
from lib_layered_config import OutputFormat as LibOutputFormat
from lib_layered_config import display_config as _lib_display

from lsdsk.adapters.config.secrets import redact_secrets
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from rich.console import Console


def display_config(
    config: Config,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    section: str | None = None,
    console: Console | None = None,
    profile: str | None = None,
) -> None:
    """Display configuration using lib_layered_config's Rich display.

    Flushes any pending log output before displaying to prevent
    mixing log messages with configuration output.

    Args:
        config: Already-loaded layered configuration object to display.
        output_format: Output format: OutputFormat.HUMAN for TOML-like display or
            OutputFormat.JSON for JSON. Defaults to OutputFormat.HUMAN.
        section: Optional section name to display only that section. When None,
            displays all configuration.
        console: Optional Rich Console for output. When None, uses the module-level
            default. Primarily useful for testing.
        profile: Optional profile name to include in provenance comments.

    Side Effects:
        Flushes pending log messages before display.
        Writes formatted configuration to stdout.

    Raises:
        ValueError: If a section was requested that doesn't exist.
    """
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.flush()

    # Redacted BEFORE the library renders it, because the human path delegates
    # the whole rendering and there is no output to post-process. The library
    # redacts too, but only where a sensitive word sits at an underscore
    # boundary, so `SmtpPassword`, `dbPassword` and `db_pass` printed in full.
    # Redacting first is idempotent: the library's pass then sees a value that
    # is already the placeholder.
    # Through with_overrides rather than by rebuilding the Config, because that
    # is the public seam and it carries the provenance map with it; constructing
    # a fresh Config dropped provenance, and the human view prints which layer
    # each value came from.
    safe = config.with_overrides(redact_secrets(config.as_dict()))
    lib_format = LibOutputFormat(output_format.value)
    _lib_display(safe, output_format=lib_format, section=section, profile=profile, console=console)


__all__ = ["display_config"]
