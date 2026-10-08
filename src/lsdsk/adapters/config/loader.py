"""Configuration loader with caching and profile/override support."""

from __future__ import annotations

import copy
import tomllib
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, cast

from lib_layered_config import (
    DEFAULT_MAX_PROFILE_LENGTH,
    Config,
    read_config,
    validate_profile_name,
)
from lib_layered_config import (
    ValidationError as _LibValidationError,
)

from lsdsk import __init__conf__
from lsdsk.domain.errors import ConfigurationError


class DamagedInstallationError(ConfigurationError):
    """A configuration file this package SHIPS could not be read.

    Its own type, so the one place that refuses it can catch exactly this and
    no other configuration error: the installation is damaged, which is a
    configuration fault of the machine and not a mistake in the command line,
    the code an ``OSError`` reaching the last-resort handler would otherwise
    leave.
    """


class ConfigLoaderProtocol(Protocol):
    """Protocol for config loader with cache_clear method."""

    def __call__(
        self, *, profile: str | None = None, start_dir: str | None = None, dotenv_path: str | None = None
    ) -> Config: ...
    def cache_clear(self) -> None: ...


def validate_profile(profile: str, max_length: int | None = None) -> None:
    """Validate profile name using lib_layered_config.

    Delegates to lib_layered_config.validate_profile_name() which provides
    comprehensive validation including length limits, character restrictions,
    Windows reserved name checks, and path traversal prevention.

    Args:
        profile: The profile name to validate.
        max_length: Optional maximum length. Defaults to DEFAULT_MAX_PROFILE_LENGTH (64).

    Raises:
        ValueError: If profile name is invalid (empty, too long, invalid chars,
            Windows reserved name, path traversal attempt, etc.).

    Examples:
        >>> validate_profile("production")  # valid, no exception

        >>> validate_profile("staging-v2")  # hyphens allowed

        >>> validate_profile("../etc/passwd")  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        ValueError: profile contains invalid characters: ../etc/passwd

        >>> validate_profile("a" * 65)  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        ValueError: profile exceeds maximum length...
    """
    length = max_length if max_length is not None else DEFAULT_MAX_PROFILE_LENGTH
    try:
        validate_profile_name(profile, max_length=length)
    except _LibValidationError as exc:
        # Normalise the dependency's exception to the documented ValueError contract so callers'
        # `except ValueError` guards catch every invalid profile uniformly.
        raise ValueError(str(exc)) from exc


@lru_cache(maxsize=1)
def get_default_config_path() -> Path:
    """Return the path to the bundled default configuration file.

    The default configuration ships with the package and needs to be
    locatable at runtime regardless of how the package is installed.
    Uses __file__ to locate the defaultconfig.toml file relative to this
    module.

    Returns:
        Absolute path to defaultconfig.toml.

    Note:
        This function is cached since the path never changes during runtime.

    Example:
        >>> path = get_default_config_path()
        >>> path.name
        'defaultconfig.toml'
        >>> path.exists()
        True
    """
    return Path(__file__).parent / "defaultconfig.toml"


# Configuration is loaded once per (profile, start_dir) tuple and cached
# for the process lifetime. Intentional for a short-lived CLI process.
@lru_cache(maxsize=4)
def _get_config_impl(
    *, profile: str | None = None, start_dir: str | None = None, dotenv_path: str | None = None
) -> Config:
    """Internal cached implementation of config loading.

    Profile validation must be done by caller before invoking this function.
    """
    return read_config(
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        profile=profile,
        default_file=get_default_config_path(),
        start_dir=start_dir,
        dotenv_path=dotenv_path,
    )


def _get_config(*, profile: str | None = None, start_dir: str | None = None, dotenv_path: str | None = None) -> Config:
    """Load layered configuration with application defaults.

    Centralizes configuration loading so all entry points use the same
    precedence rules and default values without duplicating the discovery
    logic. Uses lru_cache to avoid redundant file reads when called from
    multiple modules.

    Loads configuration from multiple sources in precedence order:
    defaults -> app -> host -> user -> dotenv -> env

    The vendor, app, and slug identifiers determine platform-specific paths:
    - Linux: Uses XDG directories with slug
    - macOS: Uses Library/Application Support with vendor/app
    - Windows: Uses ProgramData/AppData with vendor/app

    When a profile is specified, configuration is loaded from profile-specific
    subdirectories (e.g., ~/.config/slug/profile/<name>/config.toml).

    Args:
        profile: Optional profile name for environment isolation. When specified,
            a ``profile/<name>/`` subdirectory is inserted into all configuration
            paths. Valid names: alphanumeric, hyphens, underscores. Examples:
            'test', 'production', 'staging-v2'. Defaults to None (no profile).
        start_dir: Optional directory that seeds .env discovery. Defaults to current
            working directory when None.
        dotenv_path: Optional explicit path to a ``.env`` file. When set, this
            file is loaded directly instead of searching upward from *start_dir*.

    Returns:
        Immutable configuration object with provenance tracking.

    Note:
        This function is cached (maxsize=4). The first call loads and parses all
        configuration files; subsequent calls with the same parameters return the
        cached Config instance immediately.

    Example:
        >>> config = get_config()
        >>> isinstance(config.as_dict(), dict)
        True
        >>> config.get("nonexistent", default="fallback")
        'fallback'

        >>> # Load production profile
        >>> prod_config = get_config(profile="production")  # doctest: +SKIP

    See Also:
        lib_layered_config.read_config: Underlying configuration loader.
    """
    if profile is not None:
        validate_profile(profile)
    return _get_config_impl(profile=profile, start_dir=start_dir, dotenv_path=dotenv_path)


def _cache_clear() -> None:
    """Clear the internal configuration cache.

    Call this function to invalidate cached configuration and force a fresh
    read from disk on the next ``get_config()`` call. Useful in tests or when
    configuration files have been modified during runtime.

    Example:
        >>> get_config.cache_clear()  # Force re-read on next call
    """
    _get_config_impl.cache_clear()


class _ConfigLoader:
    """A callable with a ``cache_clear``, which is what the protocol asks for.

    The pair used to be built by assigning ``cache_clear`` onto the function
    object and casting the result, which needed a ``type: ignore`` because a
    function has no such attribute to a type checker. A class that declares both
    members satisfies :class:`ConfigLoaderProtocol` structurally, so neither the
    cast nor the suppression is needed and the shape is checked rather than
    asserted.
    """

    def __call__(
        self, *, profile: str | None = None, start_dir: str | None = None, dotenv_path: str | None = None
    ) -> Config:
        """Load the merged configuration.

        Args:
            profile: Environment profile to layer in, if any.
            start_dir: Directory the upward ``.env`` search starts from.
            dotenv_path: An explicit ``.env`` file, skipping that search.

        Returns:
            The merged configuration.
        """
        return _get_config(profile=profile, start_dir=start_dir, dotenv_path=dotenv_path)

    def cache_clear(self) -> None:
        """Drop the cached configuration so the next call re-reads it."""
        _cache_clear()


get_config: ConfigLoaderProtocol = _ConfigLoader()


def _deep_merge(base: dict[str, object], update: Mapping[str, object]) -> None:
    """Merge `update` into `base` in place, a nested table key by key.

    Args:
        base: The table being built.
        update: A later file's table, whose values win.

    Example:
        >>> table: dict[str, object] = {"s": {"a": 1, "b": 2}}
        >>> _deep_merge(table, {"s": {"b": 3}})
        >>> table
        {'s': {'a': 1, 'b': 3}}
    """
    for key, value in update.items():
        existing = base.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            _deep_merge(cast("dict[str, object]", existing), cast("Mapping[str, object]", value))
        else:
            base[key] = value


#: The files the package ships beside ``defaultconfig.toml``, in the order they are read.
#:
#: The directory used to be read by globbing what exists, so a deleted file was
#: indistinguishable from a package that never had it: its keys silently left the
#: shipped defaults. Naming them makes a missing one damage to the installation.
#: ``tests/test_shipped_config_damaged.py`` holds this equal to the directory's
#: own listing, so a file added there is added here or the suite says so.
SHIPPED_COMPANION_FILES: Final = (
    "40-layered-config.toml",
    "50-history.toml",
    "60-thresholds.toml",
    "70-display.toml",
    "90-logging.toml",
)


def _shipped_paths() -> list[Path]:
    """Every shipped file in reading order, naming the one that is missing when one is.

    Returns:
        ``defaultconfig.toml`` then each companion file.

    Raises:
        DamagedInstallationError: When a companion file the package is known to
            ship is not there.
    """
    base = get_default_config_path()
    companions = base.parent / f"{base.stem}.d"
    # The library's own rule for a default file: the file itself, then its
    # companion ``<stem>.d`` directory in name order.
    paths = [base, *(companions / name for name in SHIPPED_COMPANION_FILES)]
    for path in paths[1:]:
        if not path.is_file():
            message = (
                f"lsdsk's own shipped configuration is incomplete, so the installation is damaged: {path.name} is gone"
            )
            raise DamagedInstallationError(message)
    return [base, *sorted(companions.glob("*.toml"))]


@lru_cache(maxsize=1)
def _shipped_tables() -> Mapping[str, object]:
    """Every table the package ships, merged in the order the defaults layer reads them.

    Returns:
        A read-only view of the merged shipped tables.

    Raises:
        DamagedInstallationError: When a shipped file is missing, cannot be
            read, or does not parse.
    """
    merged: dict[str, object] = {}
    for path in _shipped_paths():
        try:
            table = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            message = f"lsdsk's own shipped configuration could not be read, so the installation is damaged: {error}"
            raise DamagedInstallationError(message) from error
        _deep_merge(merged, table)
    return MappingProxyType(merged)


def require_an_intact_installation() -> None:
    """Refuse a damaged installation before anything reads its shipped files.

    The layered library reads the same files first and answers each kind of
    damage its own way - a missing default is passed over, an unreadable one
    escapes as a bare ``PermissionError``, a corrupt one is its own refusal - so
    three ways for one file to be damaged gave three different exit codes, two
    of them blaming the command line. Reading them here first gives every kind
    one answer. The result is cached, so the logging runtime's later read of
    the same tables costs nothing.

    Raises:
        DamagedInstallationError: When a shipped file is missing, cannot be
            read, or does not parse.

    Example:
        >>> require_an_intact_installation()
    """
    _shipped_tables()


def shipped_section(section: str) -> dict[str, object]:
    """One section as the package ships it, before any file, variable or ``--set``.

    The merged configuration cannot answer this: once a reader overrides a key,
    the shipped value is gone from it. A fallback for a value the tool cannot use
    needs exactly that value, so it is read from the shipped files themselves.

    Args:
        section: The top-level table, ``lib_log_rich`` for example.

    Returns:
        A fresh copy of the table, empty when the package ships none.

    Example:
        >>> shipped_section("lib_log_rich")["console_level"]
        'INFO'
        >>> shipped_section("no_such_section")
        {}
    """
    table = _shipped_tables().get(section)
    if not isinstance(table, Mapping):
        return {}
    return copy.deepcopy(dict(cast("Mapping[str, object]", table)))


__all__ = [
    "SHIPPED_COMPANION_FILES",
    "DamagedInstallationError",
    "get_config",
    "get_default_config_path",
    "require_an_intact_installation",
    "shipped_section",
    "validate_profile",
]
