"""Deploy default configuration to app/host/user target directories, and write example files."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from lib_layered_config import (
    DeployModeError,
    DeployPermissionsError,
    deploy_config,
    deploy_config_paths,
    generate_examples,
)
from lib_layered_config.examples import DeployAction

from lsdsk import __init__conf__
from lsdsk.adapters.config.loader import get_default_config_path, table_leaves, validate_profile
from lsdsk.adapters.config.values import rendered
from lsdsk.domain.base import DomainModel
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.text import visible_text

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lib_layered_config import Config

    from lsdsk.domain.deployment import DeployRequest
    from lsdsk.domain.enums import DeployTarget

_DEPLOYED_ACTIONS = frozenset({DeployAction.CREATED, DeployAction.OVERWRITTEN})

#: What a refused configured mode is followed by, in this command's own options.
#: The library's hint names its own parameters (``dir_mode``, ``set_permissions=False``),
#: which a reader of ``config-deploy`` has no way to pass.
_DEPLOY_ANYWAY_HINT = (
    "to deploy anyway, give both --dir-mode and --file-mode (the built-in ones are 0o700 and 0o600 "
    "for user, 0o755 and 0o644 for app and host); --no-permissions also deploys, but leaves every "
    "mode to the umask, which can make a user file that holds secrets readable by other accounts"
)

#: The library refuses an unsafe explicit mode one line per parameter, ``dir_mode: <reason>``,
#: naming its own parameters; a reader of ``config-deploy`` passed ``--dir-mode``.
_OPTION_FOR_PARAMETER = (("dir_mode: ", "--dir-mode: "), ("file_mode: ", "--file-mode: "))


def _named_by_option(line: str) -> str:
    """One refusal line, its leading library parameter replaced by this command's option.

    Args:
        line: A line of the library's explicit-mode refusal.

    Returns:
        The line with ``dir_mode: ``/``file_mode: `` at its start named as the option;
        any other line unchanged.

    Examples:
        >>> _named_by_option("dir_mode: unsafe directory mode 0o777: world write (0o002)")
        '--dir-mode: unsafe directory mode 0o777: world write (0o002)'
        >>> _named_by_option("a line naming dir_mode: elsewhere")
        'a line naming dir_mode: elsewhere'
    """
    for parameter, option in _OPTION_FOR_PARAMETER:
        if line.startswith(parameter):
            return option + line.removeprefix(parameter)
    return line


def _refuse_a_directory_at_a_target(destinations: Iterable[Path]) -> None:
    """Refuse a target path that is a directory, which the library passes over as skipped.

    The library skips whatever it finds at a target, so a directory standing where
    ``config.toml`` belongs read as "already there" while the ``config.d`` files
    beside it were written and the run reported success with the file missing.

    Args:
        destinations: Where the library deployed, or passed over, each target.

    Raises:
        ConfigurationError: Naming the first destination that is a directory.
    """
    for destination in destinations:
        if destination.is_dir():
            message = f"{destination} is a directory, not a configuration file; move it away to deploy there"
            raise ConfigurationError(message)


def paths_the_deploy_writes(targets: Iterable[DeployTarget], *, profile: str | None) -> frozenset[Path]:
    """Every file a deploy of `targets` writes, resolved, whether or not it exists yet.

    The library's ``deploy_config_paths`` for this package: the set lib_layered_config skips
    when it reads the permission modes, so that a file never decides its own replacement - each
    target's configuration file, and in its companion ``.d`` directory a copy of every file the
    package ships in its own.

    Args:
        targets: The layers deployed to.
        profile: The profile deployed into, or ``None``.

    Returns:
        The resolved paths.

    Raises:
        FileNotFoundError: The shipped default configuration is missing.
        ValueError: `profile` is not a valid profile name.
    """
    return deploy_config_paths(
        get_default_config_path(),
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        profile=profile,
        targets=[target.value for target in targets],
    )


#: The most characters :func:`~lsdsk.domain.text.visible_text` writes for one, ``\UNNNNNNNN``.
_LONGEST_ESCAPE = 10

#: Said once after the values a deploy's new files override: how to keep each of them.
SHADOWED_VALUES_HINT = (
    "Hint: --force does not keep these values: it replaces the kept file with the shipped copy too. To keep one, "
    "set it in the file that now decides it, or move it into a config.d file of your own that sorts after "
    "the shipped ones, such as config.d/99-local.toml."
)


class ValueTheDeployShadows(DomainModel, frozen=True):
    """A value a kept file sets that a file this deploy created now overrides.

    Attributes:
        dotted: The setting, ``display.wwn_width``.
        raw: The value the kept file sets, as a reader would recognise it.
        source: The kept file that sets it.
        winner: The file this deploy created, which now decides the setting.
        winning_raw: The value that file gives it.

    Example:
        >>> ValueTheDeployShadows(
        ...     dotted="display.wwn_width", raw="40", source="/u/config.toml",
        ...     winner="/u/config.d/70-display.toml", winning_raw="24",
        ... ).as_sentence()  # doctest: +NORMALIZE_WHITESPACE
        'Warning: display.wwn_width=40 in /u/config.toml is now overridden by /u/config.d/70-display.toml,
        which this deploy created and which sets 24.'
    """

    dotted: str
    raw: str
    source: str
    winner: str
    winning_raw: str

    def as_sentence(self) -> str:
        """The warning, the values cut as every quoted value is and the paths whole, as text."""
        source = visible_text(self.source, limit=_LONGEST_ESCAPE * len(self.source))
        winner = visible_text(self.winner, limit=_LONGEST_ESCAPE * len(self.winner))
        return (
            f"Warning: {self.dotted}={visible_text(self.raw)} in {source} is now overridden by {winner}, "
            f"which this deploy created and which sets {visible_text(self.winning_raw)}."
        )


def values_the_deploy_shadows(
    before: Config, after: Config, *, written: Iterable[Path]
) -> tuple[ValueTheDeployShadows, ...]:
    """The values a file the deploy did not write set before it, and a file it wrote now overrides.

    A deploy without ``--force`` keeps a ``config.toml`` already there and creates the ``config.d``
    companions beside it, which the loader reads after it and which therefore win: a value the
    reader wrote into ``config.toml`` is replaced by the shipped one while the deploy reports
    success. Only a file of the SAME layer counts, because outranking a lower layer is what a
    deploy to a higher one is for. Read from provenance on both sides, so a value the environment,
    a ``.env`` or ``--set`` gives is never named, and a file the deploy replaced (``--force``) is
    one whose values it was asked to replace.

    Args:
        before: The configuration as the files decided it before the deploy.
        after: The configuration as the files decide it now.
        written: The files the deploy created or overwrote.

    Returns:
        One entry per overridden setting, in key order.

    Example:
        >>> from lib_layered_config import Config
        >>> values_the_deploy_shadows(Config({}, {}), Config({}, {}), written=[])
        ()
    """
    fresh = frozenset(path.resolve() for path in written)
    shadowed: list[ValueTheDeployShadows] = []
    for segments, value in table_leaves(after.as_dict()):
        dotted = ".".join(segments)
        now, then = after.origin(dotted), before.origin(dotted)
        if now is None or then is None or now["path"] is None or then["path"] is None:
            continue
        if now["layer"] != then["layer"] or before.get(dotted) == value:
            continue
        if Path(now["path"]).resolve() in fresh and Path(then["path"]).resolve() not in fresh:
            shadowed.append(
                ValueTheDeployShadows(
                    dotted=dotted,
                    raw=rendered(before.get(dotted)),
                    source=then["path"],
                    winner=now["path"],
                    winning_raw=rendered(value),
                )
            )
    return tuple(sorted(shadowed, key=lambda entry: entry.dotted))


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
        Empty when nothing was written: every target file already existed and
        ``force`` was off, or every one already held exactly the shipped content,
        which the library skips even with ``force``.

    Raises:
        PermissionError: When deploying to app/host without sufficient privileges.
        ValueError: When invalid target names are provided.
        ConfigurationError: A directory stands where a configuration file belongs.
        DeployPermissionsError: A configured or overriding mode was refused by the
            library, which decides every mode; nothing has been written. It is a
            ``ValueError``.
        DeployModeError: An explicit ``dir_mode``/``file_mode`` was refused as unsafe,
            each line naming ``--dir-mode``/``--file-mode``; nothing has been written.
            It is a ``ValueError``.

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

    try:
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
            # The library reads ``enabled`` from the configuration itself and refuses
            # a value that is not a boolean, even though this call has already decided
            # it (see above) and the caller coerced the configured one with a warning.
            # Handing it the settled answer replaces the key it would validate. The
            # request's own overrides - the command line's ``--set`` modes - go in
            # beside it, because the library's read does not include the command line.
            permission_overrides={
                **dict(request.permission_overrides),
                "enabled": request.set_permissions is not False,
            },
            dir_mode=request.dir_mode,
            file_mode=request.file_mode,
        )
    except DeployPermissionsError as refused:
        # The refusal itself is the library's: it read the section from its own layers
        # and is the one decider of a mode. Only the closing hint is reworded.
        hint = _DEPLOY_ANYWAY_HINT if refused.hint is not None else None
        raise DeployPermissionsError(refused.problems, hint=hint) from refused
    except DeployModeError as refused:
        # An unsafe --dir-mode/--file-mode: the library parsed the value and refused it,
        # which stays exit 22 (the same class); only the parameter names are this command's.
        named = "\n".join(_named_by_option(line) for line in str(refused).split("\n"))
        raise DeployModeError(named) from refused

    _refuse_a_directory_at_a_target(result.destination for result in results)
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


def _layout(*, slug: str, vendor: str, app: str) -> list[Path]:
    """The paths, relative to a destination, that this platform's examples occupy.

    Learned by writing them into a scratch directory with the library itself,
    because the layout is the library's to decide and differs per platform: a
    copy of it here would drift, and the library offers no way to ask for it
    without writing.

    Args:
        slug: The configuration slug the examples are named for.
        vendor: The vendor directory name.
        app: The application name.

    Returns:
        Each file's path relative to the destination.
    """
    with tempfile.TemporaryDirectory(prefix="lsdsk-layout-") as scratch:
        root = Path(scratch)
        written = generate_examples(root, slug=slug, vendor=vendor, app=app, force=True)
        return [path.relative_to(root) for path in written]


def generate_examples_refusing_directories(
    destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False
) -> list[Path]:
    """Write the example files, after refusing any directory that stands where one belongs.

    The library reads "something is there" as "already present": without
    ``force`` the example was left out under "all already exist", and with it
    the write failed late on ``IsADirectoryError``. A directory is neither - it
    needs moving - so it is refused before anything is written.

    Args:
        destination: The directory the examples go under.
        slug: The configuration slug the examples are named for.
        vendor: The vendor directory name.
        app: The application name.
        force: Overwrite files already there.

    Returns:
        The files written.

    Raises:
        ConfigurationError: A directory stands at a path an example would occupy.
        PermissionError: The destination may not be written.
        OSError: The files could not be written.
    """
    base = Path(destination)
    for relative in _layout(slug=slug, vendor=vendor, app=app):
        if (base / relative).is_dir():
            message = f"{base / relative} is a directory, not an example file; move it away to generate there"
            raise ConfigurationError(message)
    return generate_examples(destination, slug=slug, vendor=vendor, app=app, force=force)


__all__ = [
    "SHADOWED_VALUES_HINT",
    "ValueTheDeployShadows",
    "deploy_configuration",
    "generate_examples_refusing_directories",
    "paths_the_deploy_writes",
    "values_the_deploy_shadows",
]
