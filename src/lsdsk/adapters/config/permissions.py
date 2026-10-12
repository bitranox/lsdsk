"""Permission settings loader for config deployment.

Provides functions to load permission defaults from configuration and
compute effective permission modes for deployment targets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, assert_never

from lib_layered_config import (
    DEFAULT_APP_DIR_MODE,
    DEFAULT_APP_FILE_MODE,
    DEFAULT_USER_DIR_MODE,
    DEFAULT_USER_FILE_MODE,
)
from pydantic import BaseModel, ConfigDict

from ...domain.enums import DeployTarget
from .values import RejectedValue, SectionValues, mode_of

if TYPE_CHECKING:
    from lib_layered_config import Config


class PermissionDefaults(BaseModel):
    """Validated, immutable permission defaults for deployment layers.

    Parsed at the boundary from ``[lib_layered_config.default_permissions]``
    config section. All fields have library-level fallback defaults.

    Example:
        >>> defaults = PermissionDefaults()
        >>> defaults.user_directory == 0o700
        True
    """

    model_config = ConfigDict(frozen=True)

    app_directory: int = DEFAULT_APP_DIR_MODE
    app_file: int = DEFAULT_APP_FILE_MODE
    host_directory: int = DEFAULT_APP_DIR_MODE
    host_file: int = DEFAULT_APP_FILE_MODE
    user_directory: int = DEFAULT_USER_DIR_MODE
    user_file: int = DEFAULT_USER_FILE_MODE
    enabled: bool = True

    def dir_mode_for(self, layer: DeployTarget) -> int:
        """Return directory mode for the given deploy target.

        Spelled out rather than assembled from the member's value. Building the
        attribute name by hand means a target added later raises AttributeError
        on somebody's deploy instead of failing here at type-check time, and
        getattr hands back Any, so nothing checks the result is even an int.
        """
        match layer:
            case DeployTarget.APP:
                return self.app_directory
            case DeployTarget.HOST:
                return self.host_directory
            case DeployTarget.USER:
                return self.user_directory
        assert_never(layer)

    def file_mode_for(self, layer: DeployTarget) -> int:
        """Return file mode for the given deploy target."""
        match layer:
            case DeployTarget.APP:
                return self.app_file
            case DeployTarget.HOST:
                return self.host_file
            case DeployTarget.USER:
                return self.user_file
        assert_never(layer)


def parse_mode(value: int | str, default: int) -> int:
    """Parse a permission mode value from config.

    Accepts octal text (``"0o755"``, ``"0755"`` or ``"755"``) or a numeric int that cannot
    be octal digits. An int of only the digits 0-7 (``755``) is ambiguous - an unquoted
    ``.env`` value ``755`` arrives as that int - so it is not guessed at: this function
    returns the default, while :func:`read_permission_defaults` (the configured section) raises
    :class:`~.values.AmbiguousModeError`, which the CLI turns into a startup refusal.

    Args:
        value: Integer mode or octal string.
        default: Fallback value if parsing fails.

    Returns:
        Integer permission mode.

    Example:
        >>> parse_mode(493, 0o755)
        493
        >>> parse_mode("0o755", 0o644)
        493
        >>> parse_mode("755", 0o644)
        493
        >>> parse_mode(755, 0o644)
        420
    """
    return mode_of(value, default)


#: The table the permission defaults are read from.
PERMISSIONS_SECTION = "lib_layered_config.default_permissions"


class PermissionReading(NamedTuple):
    """The permission defaults, and every configured value refused.

    Returned as a pair for the reason :class:`~.tunables.ThresholdsReading` is:
    the value and the record of falling back come from one decision.
    """

    defaults: PermissionDefaults
    rejected: tuple[RejectedValue, ...]


def get_permission_defaults(config: Config) -> PermissionDefaults:
    """Load permission defaults from [lib_layered_config.default_permissions].

    Args:
        config: Configuration object with merged settings.

    Returns:
        The defaults alone; :func:`read_permission_defaults` also keeps what was refused.

    Example:
        >>> from lib_layered_config import Config
        >>> get_permission_defaults(Config({}, {})).enabled
        True
    """
    return read_permission_defaults(config).defaults


def read_permission_defaults(config: Config) -> PermissionReading:
    """Load permission defaults from [lib_layered_config.default_permissions].

    Reads configurable permission defaults for each deployment layer.
    Falls back to lib_layered_config library defaults if not configured.

    Args:
        config: Configuration object with merged settings.

    Returns:
        PermissionDefaults model with typed fields for each layer's
        directory and file modes, plus an enabled flag, with the values that
        could not be used. ``enabled`` is a switch, so anything but a real
        boolean falls back to true and is recorded.

    Raises:
        AmbiguousModeError: When a mode is an int of only the digits 0-7 (``755``).

    Example:
        >>> from lib_layered_config import Config
        >>> config = Config({}, {})  # Empty config
        >>> reading = read_permission_defaults(config)
        >>> reading.defaults.user_directory == 0o700
        True
        >>> read_permission_defaults(Config({"lib_layered_config": {"default_permissions": "x"}}, {})).rejected[0].raw
        'x'
    """
    # NOTE: lib_layered_config does not define separate HOST_* constants.
    # Host layer shares defaults with app layer (both world-readable: 755/644).
    # This is intentional per CLAUDE.md "Deployment Permissions" documentation.
    values = SectionValues(PERMISSIONS_SECTION, config)
    defaults = PermissionDefaults(
        app_directory=values.mode("app_directory", DEFAULT_APP_DIR_MODE),
        app_file=values.mode("app_file", DEFAULT_APP_FILE_MODE),
        host_directory=values.mode("host_directory", DEFAULT_APP_DIR_MODE),
        host_file=values.mode("host_file", DEFAULT_APP_FILE_MODE),
        user_directory=values.mode("user_directory", DEFAULT_USER_DIR_MODE),
        user_file=values.mode("user_file", DEFAULT_USER_FILE_MODE),
        enabled=values.flag("enabled", default=True),
    )
    return PermissionReading(defaults, values.rejected)


class DeployModes(NamedTuple):
    """The directory and file modes one deploy target writes with.

    Both are ints, so returned as a bare pair a swap type-checks and then makes a
    directory 0644 and a file 0755, which is a file nobody can read and one
    anybody can run.

    Attributes:
        directory: Mode for directories created on the way.
        file: Mode for the written configuration file.
    """

    directory: int
    file: int


def get_modes_for_target(
    target: DeployTarget,
    config: Config,
    *,
    dir_mode_override: int | None = None,
    file_mode_override: int | None = None,
) -> DeployModes:
    """Get dir_mode and file_mode for a target, applying overrides.

    Retrieves permission modes for a deployment target from configuration,
    then applies any CLI overrides. CLI overrides take precedence over
    configured defaults.

    Args:
        target: The deployment target layer (app, host, or user).
        config: Configuration object with merged settings.
        dir_mode_override: CLI override for directory mode. If provided,
            takes precedence over configuration.
        file_mode_override: CLI override for file mode. If provided,
            takes precedence over configuration.

    Returns:
        The directory and file modes to pass to deploy_config.
        Values are integers (octal mode values). Always returns valid modes
        since get_permission_defaults provides fallbacks for all targets.

    Example:
        >>> from lib_layered_config import Config
        >>> from lsdsk.domain.enums import DeployTarget
        >>> config = Config({}, {})
        >>> dir_mode, file_mode = get_modes_for_target(
        ...     DeployTarget.USER, config
        ... )
        >>> dir_mode == 0o700
        True
    """
    defaults = get_permission_defaults(config)

    dir_mode: int = dir_mode_override if dir_mode_override is not None else defaults.dir_mode_for(target)
    file_mode: int = file_mode_override if file_mode_override is not None else defaults.file_mode_for(target)

    return DeployModes(dir_mode, file_mode)


__all__ = [
    "PERMISSIONS_SECTION",
    "DeployModes",
    "PermissionDefaults",
    "PermissionReading",
    "get_modes_for_target",
    "get_permission_defaults",
    "parse_mode",
    "read_permission_defaults",
]
