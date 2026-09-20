"""Deploy default configuration to app/host/user target directories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib_layered_config import deploy_config
from lib_layered_config.examples.deploy import DeployAction

from lsdsk import __init__conf__
from lsdsk.adapters.config.loader import get_default_config_path, validate_profile

if TYPE_CHECKING:
    from pathlib import Path

    from lsdsk.domain.deployment import DeployRequest

_DEPLOYED_ACTIONS = frozenset({DeployAction.CREATED, DeployAction.OVERWRITTEN})


def deploy_configuration(request: DeployRequest) -> list[Path]:
    r"""Deploy default configuration to specified target layers.

    Users need to initialize configuration files in standard locations
    (application, host, or user config directories) without manually
    copying files or knowing platform-specific paths. Uses
    lib_layered_config.deploy_config() to copy the bundled defaultconfig.toml
    to requested target layers (app, host, user).

    Args:
        request: What to deploy and how. Its ``targets`` are the layers to
            write, valid values being ``DeployTarget.APP``, ``.HOST`` and
            ``.USER``, and several may be named at once; ``force`` overwrites a
            file already there rather than skipping it; ``profile`` deploys into
            the profile subdirectories (``~/.config/slug/profile/<name>/``);
            ``set_permissions`` decides whether modes are set at all, with the
            defaults 755/644 for app and host and 700/600 for user, and ``None``
            meaning the caller had no preference, which is taken as yes here;
            ``dir_mode`` and ``file_mode`` override those defaults for every
            target.

    Returns:
        List of paths where configuration files were created or would be created.
        Empty list if all target files already exist and force=False.

    Raises:
        PermissionError: When deploying to app/host without sufficient privileges.
        ValueError: When invalid target names are provided.

    Side Effects:
        Creates configuration files in platform-specific directories:
        - app: System-wide application config (requires privileges)
        - host: System-wide host config (requires privileges)
        - user: User-specific config (current user's home directory)

    Note:
        Platform-specific paths (without profile):
        - Linux (app): /etc/xdg/{slug}/config.toml
        - Linux (host): /etc/xdg/{slug}/hosts/{hostname}.toml
        - Linux (user): ~/.config/{slug}/config.toml
        - macOS (app): /Library/Application Support/{vendor}/{app}/config.toml
        - macOS (host): /Library/Application Support/{vendor}/{app}/hosts/{hostname}.toml
        - macOS (user): ~/Library/Application Support/{vendor}/{app}/config.toml
        - Windows (app): C:\ProgramData\{vendor}\{app}\config.toml
        - Windows (host): C:\ProgramData\{vendor}\{app}\hosts\{hostname}.toml
        - Windows (user): %APPDATA%\{vendor}\{app}\config.toml

        Platform-specific paths (with profile='production'):
        - Linux (user): ~/.config/{slug}/profile/production/config.toml
        - Linux (host): /etc/xdg/{slug}/profile/production/hosts/{hostname}.toml
        - etc.
    """
    if request.profile is not None:
        validate_profile(request.profile)
    source = get_default_config_path()

    # Convert enum values to strings for lib_layered_config
    target_strings = [target.value for target in request.targets]

    results = deploy_config(
        source=source,
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        profile=request.profile,
        targets=target_strings,
        force=request.force,
        # The library takes a bool. None here means the caller expressed no
        # preference, which is the CLI passing neither --permissions nor
        # --no-permissions, and the answer to that is the documented default.
        set_permissions=request.set_permissions is not False,
        dir_mode=request.dir_mode,
        file_mode=request.file_mode,
    )

    # Extract paths where files were actually created or overwritten
    paths: list[Path] = []
    for result in results:
        if result.action in _DEPLOYED_ACTIONS:
            paths.append(result.destination)
        paths.extend(
            dot_d_result.destination
            for dot_d_result in result.dot_d_results
            if dot_d_result.action in _DEPLOYED_ACTIONS
        )
    return paths


__all__ = [
    "deploy_configuration",
]
