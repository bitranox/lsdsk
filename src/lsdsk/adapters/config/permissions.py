"""What lsdsk itself reads from ``[lib_layered_config.default_permissions]``.

The modes in that section are NOT judged here. ``config-deploy`` hands the section to
lib_layered_config, which reads it from its own layers when it applies the modes and
refuses whatever it cannot use, so it is the one decider. lsdsk once judged them too, by
a rule of its own, and the two disagreed in both directions: values the deploy refused
passed lsdsk silently, and values lsdsk announced it would replace with the default were
refused by the deploy on the very run that announced it.

What stays here is what lsdsk decides on its own: whether to set modes at all (``enabled``,
settled before the deploy and handed to it), the startup refusal of an int that could be
octal digits, the ``--set`` modes the deploy is given as overrides, and which configured modes
the deploy's own read leaves out, so ``config-deploy`` can name each one rather than let the
value ``lsdsk config`` reports as in force decide nothing in silence.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple, cast

from lib_layered_config import Config
from pydantic import BaseModel, ConfigDict

from ...domain.base import DomainModel
from ...domain.text import visible_text
from .overrides import apply_overrides
from .values import RejectedValue, SectionValues, rendered

#: The table the permission settings are read from.
PERMISSIONS_SECTION = "lib_layered_config.default_permissions"

#: The section's mode settings, which the deploy alone judges.
MODE_KEYS: tuple[str, ...] = tuple(
    f"{layer}_{kind}" for layer in ("app", "host", "user") for kind in ("directory", "file")
)

#: The switch lsdsk settles itself and hands to the deploy as the answer.
ENABLED_KEY = "enabled"

#: The modes of the system-wide layers, which the deploy never takes from a user file.
_SYSTEM_MODE_KEYS: tuple[str, ...] = tuple(key for key in MODE_KEYS if not key.startswith("user_"))

#: The most characters :func:`~lsdsk.domain.text.visible_text` writes for one, ``\UNNNNNNNN``.
_LONGEST_ESCAPE = 10

#: Why the deploy leaves a value out, keyed by the provenance layer that gave it.
_DOTENV_LAYER = "dotenv"
_USER_LAYER = "user"
_NOT_FROM_DOTENV = (
    "config-deploy reads its modes from the configuration files and the environment, never from a .env file"
)
_NOT_FROM_A_USER_FILE = (
    "a user configuration file cannot set an app or host mode, so config-deploy takes it from the shipped "
    "defaults, the app and host files and the environment"
)
_NOT_FROM_A_FILE_IT_REWRITES = (
    "config-deploy rewrites that file, and a file never decides the mode of its own replacement, so it takes "
    "the mode from the shipped defaults, the files it does not write and the environment"
)
_NOT_FROM_A_FILE_IT_KEEPS = (
    "config-deploy keeps that file, because it exists and --force was not given, but reads no mode from a "
    "file it deploys to, so the files it writes take the mode from the shipped defaults, the files outside "
    "this deploy and the environment"
)


class SectionGivenAsAValueError(ValueError):
    """``--set`` gave the whole permission section as one value, which no deploy can honour.

    The deploy reads the section's modes from the configuration files, and lsdsk hands it only
    the keys ``--set`` gave inside the table plus the ``enabled`` it settled itself. A value in
    place of the table fits neither half, so whatever the deploy did would match no sentence on
    the screen: it is refused rather than warned about.
    """

    def __init__(self, raw: object) -> None:
        """Word the refusal around the value that was given.

        Args:
            raw: What ``--set`` gave for the section.
        """
        super().__init__(
            f"--set {PERMISSIONS_SECTION}={visible_text(rendered(raw))} gives the section as a single value, "
            "which config-deploy cannot honour: it must be a table. Set a key inside it instead, "
            f"such as --set {PERMISSIONS_SECTION}.user_file=0o600"
        )


class PermissionDefaults(BaseModel):
    """The permission setting lsdsk decides itself, validated at the boundary.

    Example:
        >>> PermissionDefaults().enabled
        True
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True


class PermissionReading(NamedTuple):
    """The permission settings, and every configured value refused.

    Returned as a pair for the reason :class:`~.tunables.ThresholdsReading` is:
    the value and the record of falling back come from one decision.
    """

    defaults: PermissionDefaults
    rejected: tuple[RejectedValue, ...]


class ModeTheDeployIgnores(DomainModel, frozen=True):
    """A configured mode the deploy leaves out of its own read, and why.

    Attributes:
        dotted: The setting, ``lib_layered_config.default_permissions.user_file``.
        raw: The value configured, as a reader would recognise it.
        source: The file that gave it.
        reason: Why the deploy does not use it.

    Example:
        >>> ModeTheDeployIgnores(
        ...     dotted="lib_layered_config.default_permissions.user_file", raw="0o604", source="/x/.env", reason="no"
        ... ).as_sentence()
        'Warning: ignoring lib_layered_config.default_permissions.user_file=0o604 from /x/.env: no.'
    """

    dotted: str
    raw: str
    source: str
    reason: str

    def as_sentence(self) -> str:
        """The warning, with the value and the path quoted as visible text.

        The value is cut as every quoted value is. The path is not, because a reader has to
        find that file: only its control characters are written as escapes.
        """
        source = visible_text(self.source, limit=_LONGEST_ESCAPE * len(self.source))
        return f"Warning: ignoring {self.dotted}={visible_text(self.raw)} from {source}: {self.reason}."


def _why_the_deploy_ignores(
    key: str, origin: Mapping[str, object], written: frozenset[Path], kept: frozenset[Path]
) -> str | None:
    """The reason the deploy leaves `key` out when `origin` gave it, or ``None`` when it uses it.

    A file among those the deploy writes is skipped by its read whether the run replaces it or
    keeps it, so the two get one decision and the sentence says which of the two the run does.
    """
    layer = origin.get("layer")
    if layer == _DOTENV_LAYER:
        return _NOT_FROM_DOTENV
    if layer == _USER_LAYER and key in _SYSTEM_MODE_KEYS:
        return _NOT_FROM_A_USER_FILE
    path = origin.get("path")
    if path is None:
        return None
    resolved = Path(str(path)).resolve()
    if resolved in kept:
        return _NOT_FROM_A_FILE_IT_KEEPS
    if resolved in written:
        return _NOT_FROM_A_FILE_IT_REWRITES
    return None


def modes_the_deploy_ignores(
    config: Config, *, used: Config, written: frozenset[Path], kept: frozenset[Path]
) -> tuple[ModeTheDeployIgnores, ...]:
    """The configured modes the deploy's own read of its layers leaves out, where that changes the mode.

    The deploy reads ``[lib_layered_config.default_permissions]`` from the shipped defaults,
    the app, host and user files and the environment, and from a user file it takes only the
    user layer's modes. ``lsdsk config`` shows the merged configuration, which also holds a
    ``.env`` file and a user file's ``app_*``/``host_*`` modes, as the values in force. Read
    from provenance, so a mode ``--set`` gives - handed to the deploy as an override - and one
    the environment gives are never named.

    A left-out mode is named only where the value in force differs from the one the deploy
    reads. A deployed user file is a copy of the shipped one and carries the shipped
    ``app_*``/``host_*`` modes, which the deploy reads from the shipped defaults anyway:
    naming them told every later deploy about four values nobody configured.

    The files the deploy writes are skipped by its read, so a mode set in one of them - a
    ``user_file`` in the copy ``--target user`` rewrites - decides nothing either. Valid or not,
    that value is in force as far as ``lsdsk config`` says, and nothing else ever mentions it.

    Args:
        config: The configuration of the profile being deployed.
        used: The configuration the deploy itself reads its modes from.
        written: The resolved files the deploy writes, which its read skips. Required, because
            without it a mode in a rewritten file is the one case named nowhere.
        kept: Those of `written` the run leaves as they are - each one already there, on a run
            without ``--force``. Required, because the sentence for a rewritten file says the
            opposite of what the run does to a kept one.

    Returns:
        One entry per mode setting the deploy will not use, in :data:`MODE_KEYS` order.

    Example:
        >>> modes_the_deploy_ignores(Config({}, {}), used=Config({}, {}), written=frozenset(), kept=frozenset())
        ()
    """
    ignored: list[ModeTheDeployIgnores] = []
    for key in MODE_KEYS:
        dotted = f"{PERMISSIONS_SECTION}.{key}"
        origin = config.origin(dotted)
        if origin is None or config.get(dotted) == used.get(dotted):
            continue
        reason = _why_the_deploy_ignores(key, origin, written, kept)
        if reason is not None:
            source = origin.get("path")
            ignored.append(
                ModeTheDeployIgnores(dotted=dotted, raw=rendered(config.get(dotted)), source=str(source), reason=reason)
            )
    return tuple(ignored)


def read_permission_defaults(config: Config) -> PermissionReading:
    """Read what lsdsk decides itself from [lib_layered_config.default_permissions].

    Args:
        config: Configuration object with merged settings.

    Returns:
        The ``enabled`` switch, which falls back to true and is recorded when it is
        anything but a real boolean, with the values that could not be used.

    Raises:
        AmbiguousModeError: When a mode is an int of only the digits 0-7 (``755``).

    Example:
        >>> read_permission_defaults(Config({}, {})).defaults.enabled
        True
        >>> read_permission_defaults(Config({"lib_layered_config": {"default_permissions": "x"}}, {})).rejected[0].raw
        'x'
    """
    values = SectionValues(PERMISSIONS_SECTION, config)
    for key in MODE_KEYS:
        values.refuse_an_ambiguous_mode(key)
    defaults = PermissionDefaults(enabled=values.flag(ENABLED_KEY, default=True))
    return PermissionReading(defaults, values.rejected)


def permission_overrides_from(set_overrides: tuple[str, ...]) -> tuple[tuple[str, object], ...]:
    """The mode settings ``--set`` gave, as the overrides the deploy applies.

    ``lsdsk config`` reports a ``--set`` mode under the ``cli`` layer as in force, and
    the deploy's own read of the configuration does not include the command line, so
    without this the value was shown as applied and decided nothing. Read from the
    ``--set`` strings alone, merged the way the root group merges them, so a mode given
    in a file is still read by the deploy from that file with its provenance.

    ``enabled`` is left out: lsdsk settles it itself and hands the deploy the answer.

    Args:
        set_overrides: The raw ``SECTION.KEY=VALUE`` strings from the command line.

    Returns:
        ``(setting, value)`` pairs, as typed; the deploy validates each.

    Raises:
        SectionGivenAsAValueError: ``--set`` gave the section itself a value that is not a table.

    Example:
        >>> permission_overrides_from(("lib_layered_config.default_permissions.user_file=0o640",))
        (('user_file', '0o640'),)
        >>> permission_overrides_from(("lib_log_rich.console_level=DEBUG",))
        ()
    """
    if not set_overrides:
        return ()
    section = apply_overrides(Config({}, {}), set_overrides).get(PERMISSIONS_SECTION)
    if section is None:
        return ()
    if not isinstance(section, Mapping):
        # Not a fallback: read_permission_defaults would settle ``enabled`` from the shipped
        # section while the deploy, which does not read the command line, applied the modes
        # the files configure - neither of which is what such a value asks for.
        raise SectionGivenAsAValueError(section)
    table = cast("Mapping[str, object]", section)
    return tuple((name, value) for name, value in table.items() if name != ENABLED_KEY)


__all__ = [
    "ENABLED_KEY",
    "MODE_KEYS",
    "PERMISSIONS_SECTION",
    "ModeTheDeployIgnores",
    "PermissionDefaults",
    "PermissionReading",
    "SectionGivenAsAValueError",
    "modes_the_deploy_ignores",
    "permission_overrides_from",
    "read_permission_defaults",
]
