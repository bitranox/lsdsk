"""Application ports - callable Protocol definitions for adapter functions.

Each Protocol class defines a ``__call__`` method whose signature exactly
matches the corresponding adapter function.  Existing module-level functions
satisfy these protocols automatically via structural subtyping (PEP 544).

A port is here because something CROSSES it. Three were here that nothing did -
``ReadHistory``, ``WriteHistory`` and ``GetDefaultConfigPath`` - and an unused
port is worse than a missing one: it reads as an injection point, so a test
wiring the testing container believed it had an in-memory history store and
wrote to the real one. The counter store is injected by its PATH instead
(``--history-file``), which is a real seam at the filesystem boundary and the
one every history test already uses; the default config path is resolved
adapter-to-adapter, by ``config/deploy.py`` and ``config/loader.py``, and never
belonged to a container.

System Role:
    Sits between domain and adapters.  Infrastructure types such as ``Config``
    are imported under ``TYPE_CHECKING`` only so that import-linter layer
    contracts remain satisfied at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from lib_layered_config import Config

    from ..domain.deployment import DeployRequest
    from ..domain.enums import OutputFormat


class PrintInfo(Protocol):
    """Write the package's own metadata to stdout.

    A port rather than a module attribute because it is the only CLI-reachable
    behaviour the tests could not inject, so the two that needed it patched the
    project's own module instead. Everything else already arrives through
    ``AppServices``.
    """

    def __call__(self) -> None: ...


class GetConfig(Protocol):
    """Load layered configuration with application defaults."""

    def __call__(
        self, *, profile: str | None = ..., start_dir: str | None = ..., dotenv_path: str | None = ...
    ) -> Config: ...


class DeployConfiguration(Protocol):
    """Deploy default configuration to the layers one request names."""

    def __call__(self, request: DeployRequest) -> list[Path]: ...


class DisplayConfig(Protocol):
    """Display the provided configuration in the requested format."""

    def __call__(
        self, config: Config, *, output_format: OutputFormat = ..., section: str | None = ..., profile: str | None = ...
    ) -> None: ...


class InitLogging(Protocol):
    """Initialize lib_log_rich runtime with the provided configuration."""

    def __call__(self, config: Config) -> None: ...


__all__ = [
    "DeployConfiguration",
    "DisplayConfig",
    "GetConfig",
    "InitLogging",
    "PrintInfo",
]
