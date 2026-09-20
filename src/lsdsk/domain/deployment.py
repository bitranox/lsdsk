"""What one configuration deployment asks for, as a single value.

Six values - the targets, whether to overwrite, the profile, and the three
permission settings - travelled as six parameters through five signatures, and
27 of the 30 slots were forwarding or dead. That satisfies both limbs of the
test CLAUDE.md records for introducing a type: a parameter-name group in three
or more signatures that is not a framework's, AND a parameter that is forwarded
and never read.

The cost of six loose parameters is not typing them out. It is that each hop
can drop one silently: every slot is a place where a value can be forwarded
under the wrong name, defaulted rather than passed, or left out of one signature
of the five. Threaded whole, the mismatch is unrepresentable - a hop either has
the request or does not compile.

System Role:
    Domain value. Imports the domain's own base and enums and nothing else, so
    every layer may depend on it.
"""

from __future__ import annotations

from .base import DomainModel
from .enums import DeployTarget


class DeployRequest(DomainModel, frozen=True):
    """One deployment, as the caller asked for it.

    Attributes:
        targets: The layers to write, in the order the caller named them.
        force: Whether to overwrite a file that is already there.
        profile: The profile to deploy under, or ``None`` for the plain
            directories.
        set_permissions: Whether to set modes at all. ``None`` means the
            configured default decides, which is what the CLI passes when
            neither ``--permissions`` nor ``--no-permissions`` was given.
        dir_mode: The directory mode to use instead of the layer's default.
        file_mode: The file mode to use instead of the layer's default.

    Example:
        >>> request = DeployRequest(targets=(DeployTarget.USER,))
        >>> request.force, request.profile, request.set_permissions
        (False, None, None)
        >>> DeployRequest(targets=(DeployTarget.USER,), dir_mode=0o750).dir_mode
        488
    """

    targets: tuple[DeployTarget, ...]
    force: bool = False
    profile: str | None = None
    set_permissions: bool | None = None
    dir_mode: int | None = None
    file_mode: int | None = None


__all__ = ["DeployRequest"]
