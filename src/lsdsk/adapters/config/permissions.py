"""What lsdsk itself reads from ``[lib_layered_config.default_permissions]``.

The modes in that section are NOT judged here. ``config-deploy`` hands the section to
lib_layered_config, which reads it from its own layers when it applies the modes and
refuses whatever it cannot use, so it is the one decider. lsdsk once judged them too, by
a rule of its own, and the two disagreed in both directions: values the deploy refused
passed lsdsk silently, and values lsdsk announced it would replace with the default were
refused by the deploy on the very run that announced it.

What stays here is what lsdsk decides on its own: whether to set modes at all (``enabled``,
settled before the deploy and handed to it), the startup refusal of an int that could be
octal digits, and the ``--set`` modes the deploy is given as overrides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple, cast

from lib_layered_config import Config
from pydantic import BaseModel, ConfigDict

from .overrides import apply_overrides
from .values import RejectedValue, SectionValues

#: The table the permission settings are read from.
PERMISSIONS_SECTION = "lib_layered_config.default_permissions"

#: The section's mode settings, which the deploy alone judges.
MODE_KEYS: tuple[str, ...] = tuple(
    f"{layer}_{kind}" for layer in ("app", "host", "user") for kind in ("directory", "file")
)

#: The switch lsdsk settles itself and hands to the deploy as the answer.
ENABLED_KEY = "enabled"


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

    Example:
        >>> permission_overrides_from(("lib_layered_config.default_permissions.user_file=0o640",))
        (('user_file', '0o640'),)
        >>> permission_overrides_from(("lib_log_rich.console_level=DEBUG",))
        ()
    """
    if not set_overrides:
        return ()
    section = apply_overrides(Config({}, {}), set_overrides).get(PERMISSIONS_SECTION)
    if not isinstance(section, Mapping):
        # Nothing given, or a section that is not a table: the latter is reported by
        # read_permission_defaults, which falls back for it, and the deploy - which
        # does not read the command line - is then exactly what that report says.
        return ()
    table = cast("Mapping[str, object]", section)
    return tuple((name, value) for name, value in table.items() if name != ENABLED_KEY)


__all__ = [
    "ENABLED_KEY",
    "MODE_KEYS",
    "PERMISSIONS_SECTION",
    "PermissionDefaults",
    "PermissionReading",
    "permission_overrides_from",
    "read_permission_defaults",
]
