"""In-memory configuration adapters for testing.

Provides configuration functions that satisfy the same Protocols as
production adapters but operate entirely in memory -- no filesystem,
no lib_layered_config.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from lib_layered_config import Config

from ...domain.enums import OutputFormat

if TYPE_CHECKING:
    from ...domain.deployment import DeployRequest


def get_config_in_memory(
    *,
    profile: str | None = None,
    start_dir: str | None = None,
    dotenv_path: str | None = None,
) -> Config:
    """Return an empty in-memory Config.

    Args:
        profile: Accepted and ignored, so the signature matches the real loader.
        start_dir: Accepted and ignored, for the same reason.
        dotenv_path: Accepted and ignored, for the same reason.

    Returns:
        A Config with no layers, so every setting falls back to its shipped value.
    """
    return Config({}, {})


def get_default_config_path_in_memory() -> Path:
    """Return a synthetic path (not a real file).

    Returns:
        A path nothing is written to, so a test cannot reach the developer's own.
    """
    return Path(tempfile.gettempdir()) / "lsdsk" / "defaultconfig.toml"


def deploy_configuration_in_memory(request: DeployRequest) -> list[Path]:
    """Simulate deployment -- no filesystem changes, returns empty list.

    Args:
        request: Accepted and ignored, so the signature matches the real
            adapter's. One parameter rather than six, which is the point of the
            request: a double cannot now drift from the port by forgetting one.

    Returns:
        An empty list, since nothing was written.
    """
    return []


def display_config_in_memory(
    config: Config,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    section: str | None = None,
    profile: str | None = None,
) -> None:
    """No-op display -- satisfies the DisplayConfig protocol.

    Args:
        config: Accepted and ignored.
        output_format: Accepted and ignored.
        section: Accepted and ignored.
        profile: Accepted and ignored.
    """


__all__ = [
    "deploy_configuration_in_memory",
    "display_config_in_memory",
    "get_config_in_memory",
    "get_default_config_path_in_memory",
]
