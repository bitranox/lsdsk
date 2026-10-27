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
    from collections.abc import Sequence
    from pathlib import Path

    from lib_layered_config import Config
    from rich.console import Console

    from ..domain.deployment import DeployRequest
    from ..domain.elevation import Elevation
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
    """Deploy default configuration to the layers one request names.

    Raises ``PermissionError`` for a layer it may not write, ``OSError`` for any
    other failed write, ``ValueError`` for a rejected profile name, and
    :class:`~lsdsk.domain.errors.ConfigurationError` for a configuration it
    cannot use. The command maps each to its own exit code.
    """

    def __call__(self, request: DeployRequest) -> list[Path]: ...


class GenerateExamples(Protocol):
    """Write example configuration files into a directory.

    Here because six tests cross it. Every other adapter call the config
    commands make already arrives through the container, and this one did not,
    so those tests reached into the command module and replaced its own
    attribute - proving that the command forwards a flag to a function the test
    installed, and nothing about the one it really calls.

    The signature is the library's, ``platform`` excepted: nothing here passes
    it, and a port states what the caller needs rather than everything the
    implementation offers.

    Raises ``ConfigurationError`` for a directory standing where an example file
    belongs, ``PermissionError`` for a destination it may not write and
    ``OSError`` for any other failed write.
    """

    def __call__(
        self, destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = ...
    ) -> list[Path]: ...


class DisplayConfig(Protocol):
    """Display the provided configuration in the requested format."""

    def __call__(
        self,
        config: Config,
        *,
        output_format: OutputFormat = ...,
        section: str | None = ...,
        profile: str | None = ...,
        console: Console | None = ...,
    ) -> None: ...


class InitLogging(Protocol):
    """Initialize lib_log_rich runtime with the provided configuration.

    ``dotenv_path`` is the ``.env`` the configuration was loaded from, so logging
    reads its ``LOG_*`` lines from the same file.
    """

    def __call__(self, config: Config, *, dotenv_path: str | None = None) -> None: ...


class ElevateForTheView(Protocol):
    """Ask for Administrator before the interactive view reads, where that can help.

    A port because both ways into the view cross it and a test has to answer it:
    the real one shows a Windows prompt and can start a second process.
    """

    def __call__(self, replay: Path | None, *, wanted: bool) -> Elevation: ...


class ShowsStartupProgress(Protocol):
    """The line an interactive start shows until the view takes the screen.

    A port because the entry point starts it before the CLI is imported and the
    router has to give way to every prompt and stop it before the view draws;
    a test watches those steps without a terminal.
    """

    def start(self) -> None:
        """Draw the line; the entry point does this before the CLI is imported."""
        ...

    def pause(self) -> None:
        """Erase the line and hand the streams back, so a prompt starts on a clean line."""
        ...

    def resume(self) -> None:
        """Draw the line again after a pause; nothing once stopped."""
        ...

    def stop(self) -> None:
        """Erase the line for good."""
        ...


class DesktopShortcuts(Protocol):
    """Create and find the desktop shortcut that launches lsdsk, per platform.

    A port because the three platforms do genuinely different things - a Windows
    ``.lnk`` through pwshpy, a Linux ``.desktop`` file, a macOS bundle - and a
    test cannot run the real one off its own platform, so it injects a fake at
    this seam and the orchestration above is driven on every runner.
    """

    def exists(self) -> bool:
        """Whether the shortcut this platform would create is already there."""
        ...

    def has_desktop(self) -> bool:
        """Whether this account has a desktop folder to put a shortcut on; a headless server has none."""
        ...

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        """Create (or refresh) the shortcut to run ``launch``, with ``icon`` if given.

        Returns the path written.
        """
        ...

    def no_desktop_reason(self) -> str:
        """Why :meth:`has_desktop` is False, as one clause a refusal can carry.

        The adapter knows which rule turned the desktop off - a folder that is
        missing, or a configuration that names the home folder itself - and the
        reader is told that one rather than a guess built from the path.
        """
        ...


__all__ = [
    "DeployConfiguration",
    "DesktopShortcuts",
    "DisplayConfig",
    "ElevateForTheView",
    "GenerateExamples",
    "GetConfig",
    "InitLogging",
    "PrintInfo",
    "ShowsStartupProgress",
]
