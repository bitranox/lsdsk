"""Capture a machine's storage subsystem to JSON, and replay it.

A snapshot is the raw reading, not the rendered result, so replaying one runs
the identical decode, mapping and diagnosis path a live run takes.  That makes
it three useful things at once: a bug report that can be reproduced exactly, a
test fixture that exercises production code, and a way to look at a server's
storage from somewhere else.

System Role:
    Adapter layer.  Chooses the platform reader, and is the only place that
    knows a snapshot has a platform at all.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field, ValidationError

from ...domain.enums import Platform
from ...domain.errors import ConfigurationError, UnsupportedPlatformError
from ..textfile import read_text_bounded
from .linux import builder as linux_builder

if TYPE_CHECKING:
    from ...domain.models import Inventory

SCHEMA_VERSION = 2

# Schema 1 captures carry everything schema 2 does except ``captured_at``, so
# they replay unchanged and only lose the timestamp on a folded-in sample.
OLDEST_READABLE_SCHEMA = 1

# Owner-only, matching this project's convention for a user-scoped file: a
# snapshot carries the hostname, the kernel and every drive's serial number.
SNAPSHOT_FILE_MODE = 0o600


class CaptureEnvelope(BaseModel):
    """The fixed outer shape of a snapshot, validated before anything reads it.

    ``--replay`` accepts a file from any machine, so this is a trust boundary and
    the only one a snapshot crosses. The envelope's own keys are a schema and are
    checked here; everything beneath them is a map keyed by data the foreign
    machine chose (a PCI address, a device node, a sysfs class), so those stay
    mappings and are interpreted by the platform builder.

    Validating only the outer shape is the point. Without it a malformed file
    fails much later and much deeper, as a ``KeyError`` inside a builder, which
    reads as a bug in lsdsk rather than as a bad input file.

    Every field below is REQUIRED, and that is the whole guard. Given defaults
    instead, an empty object validated: replaying any JSON file at all reported a
    machine called "unknown" with no disks and nothing wrong, exit 0, and blamed
    the absent readings on privilege. A tool whose first rule is never to report
    what it did not measure has to refuse the file rather than describe it.

    Example:
        >>> CaptureEnvelope(schema=1, platform="linux", hostname="example",
        ...                 kernel="6.1.0", pci={}).platform
        <Platform.LINUX: 'linux'>
    """

    model_config = {"extra": "allow"}

    schema_version: int = Field(alias="schema")
    platform: Platform
    hostname: str
    kernel: str
    pci: dict[str, Any]
    captured_at: str | None = None


def current_platform() -> str:
    """Return the platform key for the machine this is running on.

    Example:
        >>> current_platform() in {"linux", "win32", "darwin"} or True
        True
    """
    return sys.platform


def read_current_machine() -> dict[str, Any]:
    """Read this machine's storage subsystem.

    Returns:
        A JSON-serialisable reading.

    Raises:
        ConfigurationError: If this platform has no reader.
    """
    platform = current_platform()
    if platform.startswith(Platform.LINUX):
        from .linux.reader import read_system  # noqa: PLC0415 - platform module, only importable on Linux

        return read_system()
    if platform == Platform.WINDOWS:
        from .windows.reader import read_system as read_windows  # noqa: PLC0415 - platform module

        return read_windows()
    message = (
        f"lsdsk cannot read hardware on {platform!r}. It supports Linux and Windows; "
        "on any platform you can still render a snapshot captured elsewhere with --replay."
    )
    raise UnsupportedPlatformError(message)


def build_from(capture: dict[str, Any]) -> Inventory:
    """Turn a reading into an inventory, whichever platform produced it.

    Args:
        capture: A reading, live or loaded from a snapshot.

    Returns:
        The machine as the domain sees it.

    Raises:
        ConfigurationError: If the snapshot names a platform with no builder.

    Example:
        >>> build_from({"platform": "linux", "hostname": "example"}).hostname
        'example'
    """
    platform = str(capture.get("platform", Platform.LINUX))
    if platform.startswith(Platform.LINUX):
        return linux_builder.build_inventory(capture)
    if platform == Platform.WINDOWS:
        from .windows.builder import build_inventory as build_windows  # noqa: PLC0415 - platform module

        return build_windows(capture)
    message = f"This snapshot came from {platform!r}, which lsdsk cannot interpret."
    raise ConfigurationError(message)


def collect() -> Inventory:
    """Read this machine and turn it into an inventory.

    Returns:
        The machine as the domain sees it.

    Raises:
        ConfigurationError: If this platform has no reader.
    """
    return build_from(read_current_machine())


def save(capture: dict[str, Any], path: Path) -> None:
    """Write a reading to a snapshot file, readable only by its owner.

    A snapshot names the machine, its kernel and every drive's serial number, and
    the run that produces the most complete one is a privileged run. Left at the
    ambient umask it lands group- and world-readable, so it is narrowed to the
    same mode this project already uses for a user-scoped file. Widening it to
    share the capture is then a deliberate act rather than the default.

    The write goes to a temporary file in the destination's directory and is
    then renamed over it, which is what makes the mode above worth anything.
    Writing to the path directly follows a symlink sitting there, so a snapshot
    taken as root into a directory somebody else can write lets them choose
    which file gets replaced, and narrowing the mode afterwards then narrows
    *their* target. A rename never follows the last component, so the link is
    replaced rather than traversed, and the file is never briefly world-readable
    at the ambient umask on its way to 0600.

    Args:
        capture: The reading to store.
        path: Destination file.

    Raises:
        OSError: If the file cannot be written or renamed into place. Any
            previous file at the destination is untouched in that case.
    """
    body = json.dumps(capture, indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    except OSError:
        # The temporary file lives in the DESTINATION'S directory, so a
        # destination that is writable inside a directory that is not - a
        # user-writable file under a root-owned path, or `-o /dev/null`, whose
        # parent is `/dev` - cannot be written atomically at all. Fall back to
        # writing in place with O_NOFOLLOW, which still refuses a symlink and so
        # keeps the property that matters; only the atomicity is given up, and
        # only where it was never available.
        _write_in_place(path, body)
        return
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp already creates at 0600; setting it explicitly means the
        # guarantee does not rest on that, and a umask cannot widen it.
        # A filesystem that does not carry modes is not a failure to write.
        with contextlib.suppress(OSError):
            temporary.chmod(SNAPSHOT_FILE_MODE)
        temporary.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _write_in_place(path: Path, body: str) -> None:
    """Write without a temporary file, still refusing a symlink at the destination.

    ``O_NOFOLLOW`` fails rather than opening the target of a symlink, which is
    the whole of what the rename bought against a hostile destination. What is
    lost is atomicity: an interrupted write here leaves a partial file, where a
    rename would have left the previous one. That trade is only ever taken on a
    path where no temporary file could be created, so the alternative is not
    writing at all.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, SNAPSHOT_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(body)
    with contextlib.suppress(OSError):
        path.chmod(SNAPSHOT_FILE_MODE)


def load(path: Path) -> Inventory:
    """Read a snapshot file and turn it into an inventory.

    Args:
        path: A snapshot written by :func:`save`.

    Returns:
        The captured machine as the domain sees it.

    Raises:
        ConfigurationError: If the file is not a snapshot this version understands.
    """
    try:
        payload: Any = json.loads(read_text_bounded(path, what="a snapshot"))
    # Not only JSONDecodeError: an integer literal past CPython's
    # digit limit raises a bare ValueError, and deeply nested JSON
    # exhausts the C stack with RecursionError. Both used to escape as a
    # traceback under the wrong exit code, when the honest answer is the
    # same refusal any other malformed file gets.
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        message = f"Could not read the snapshot at {path}: {error}"
        raise ConfigurationError(message) from error

    if not isinstance(payload, dict):
        message = f"{path} does not contain a snapshot object."
        raise ConfigurationError(message)

    # json.loads is Any by nature; the isinstance check above is what makes
    # this cast true, and a cast keeps the rest of the line checked.
    capture: dict[str, Any] = cast("dict[str, Any]", payload)
    try:
        envelope = CaptureEnvelope.model_validate(capture)
    except ValidationError as error:
        message = f"{path} is not a snapshot lsdsk understands: {error}"
        raise ConfigurationError(message) from error
    if not OLDEST_READABLE_SCHEMA <= envelope.schema_version <= SCHEMA_VERSION:
        message = (
            f"{path} is a schema {envelope.schema_version!r} snapshot; "
            f"this version of lsdsk reads schema {OLDEST_READABLE_SCHEMA} to {SCHEMA_VERSION}."
        )
        raise ConfigurationError(message)
    return build_from(capture)


__all__ = [
    "OLDEST_READABLE_SCHEMA",
    "SCHEMA_VERSION",
    "build_from",
    "collect",
    "current_platform",
    "load",
    "read_current_machine",
    "save",
]
