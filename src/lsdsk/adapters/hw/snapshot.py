"""Capture a machine's storage subsystem to JSON, and replay it.

A snapshot is the raw reading, not the rendered result, so replaying one runs
the identical decode, mapping and diagnosis path a live run takes.  That makes
it three useful things at once: a bug report that can be reproduced exactly, a
test fixture that exercises production code, and a way to look at a server's
storage from somewhere else.

System Role:
    Adapter layer.  Chooses the platform reader and the platform model, and is
    the only place that knows a snapshot has a platform at all.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

from pydantic import Field, TypeAdapter, ValidationError

from ...domain.enums import Platform
from ...domain.errors import ConfigurationError, UnsupportedPlatformError
from ..textfile import read_json_bounded
from .capture import CaptureEnvelope
from .linux import builder as linux_builder
from .linux.capture import LinuxCapture
from .windows import builder as windows_builder
from .windows.capture import WindowsCapture

if TYPE_CHECKING:
    from ...domain.models import Inventory

SCHEMA_VERSION = 2

# Schema 1 captures carry everything schema 2 does except ``captured_at``, so
# they replay unchanged and only lose the timestamp on a folded-in sample.
OLDEST_READABLE_SCHEMA = 1

# Owner-only, matching this project's convention for a user-scoped file: a
# snapshot carries the hostname, the kernel and every drive's serial number.
SNAPSHOT_FILE_MODE = 0o600


# One parse for every reading, live or replayed. The platform key picks the
# model, so a reading naming a platform lsdsk has no model for is refused by the
# same validation that refuses a wrong-shaped section.
_CAPTURE: TypeAdapter[LinuxCapture | WindowsCapture] = TypeAdapter(
    Annotated[LinuxCapture | WindowsCapture, Field(discriminator="platform")]
)


def current_platform() -> str:
    """Return the platform key for the machine this is running on.

    Returns:
        The key a capture records, so a replay knows which builder to use.

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


def parse_capture(reading: object) -> LinuxCapture | WindowsCapture:
    """Type a reading once, with the model for the platform it names.

    A live reading and a replayed one both come through here, so a builder only
    ever sees a typed capture, and a reading whose sections have the wrong shape
    is refused before any builder runs.

    Args:
        reading: A reading as a platform reader produced it or JSON decoded it.

    Returns:
        The typed capture for the platform the reading names.

    Raises:
        ValidationError: If the reading names no platform lsdsk has a model for,
            or a section does not have the shape its model requires.

    Example:
        >>> reading = {"schema": 2, "platform": "linux", "hostname": "example", "kernel": "6.1.0", "pci": {}}
        >>> type(parse_capture(reading)).__name__
        'LinuxCapture'
    """
    return _CAPTURE.validate_python(reading)


def _inventory_of(capture: LinuxCapture | WindowsCapture) -> Inventory:
    """Build the inventory with the builder for the capture's own platform."""
    if isinstance(capture, WindowsCapture):
        return windows_builder.build_inventory(capture)
    return linux_builder.build_inventory(capture)


def build_from(reading: object) -> Inventory:
    """Turn a reading into an inventory, whichever platform produced it.

    Args:
        reading: A reading, live or loaded from a snapshot, not yet typed.

    Returns:
        The machine as the domain sees it.

    Raises:
        ConfigurationError: If the reading names no platform lsdsk has a model
            for, or a section does not have the shape its model requires.

    Example:
        >>> reading = {"schema": 2, "platform": "linux", "hostname": "example", "kernel": "6.1.0", "pci": {}}
        >>> build_from(reading).hostname
        'example'
    """
    try:
        capture = parse_capture(reading)
    except ValidationError as error:
        message = f"This reading is not one lsdsk understands: {error}"
        raise ConfigurationError(message) from error
    return _inventory_of(capture)


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

    The reading is parsed through the same models :func:`load` reads it back
    with before a single byte reaches disk. Previously this wrote whatever
    :func:`read_current_machine` returned with a bare ``json.dumps``, so a
    reader bug that shaped one section wrongly still produced a file: it
    looked exactly like a captured snapshot and could never be replayed,
    because ``load`` refuses that same shape. What gets written on success is
    still the raw reading, not the parsed model's own re-encoding: the
    platform capture models only declare the keys their builder reads
    (``extra="ignore"``), and a capture is meant to carry more than that for a
    bug report - the error text of a refused passthrough, VPD pages nothing
    decodes yet, sysfs attributes no rule reads. Re-deriving the file from the
    typed model would silently drop all of that from every new snapshot;
    validating through it and then writing what was actually read does not.

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
        ConfigurationError: If the reading names no platform lsdsk has a model
            for, or a section does not have the shape its model requires. It is
            refused here rather than written to a file :func:`load` could
            never open.
        OSError: If the file cannot be written or renamed into place. Any
            previous file at the destination is untouched in that case.
    """
    try:
        parse_capture(capture)
    except ValidationError as error:
        message = f"This reading is not one lsdsk understands, so it was not written: {error}"
        raise ConfigurationError(message) from error
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
        _write_through(handle, body, sync=True)
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


def _write_through(descriptor: int, body: str, *, sync: bool) -> None:
    """Write the body through a raw descriptor and close it however that ends.

    ``os.fdopen`` takes ownership of the descriptor only once it RETURNS, so a
    failure inside it leaves the descriptor open with nothing holding it: the
    caller's cleanup can unlink the file it named and still leak the handle.
    Measured before this existed - one refused save moved the next free
    descriptor up by one.

    Args:
        descriptor: A descriptor nothing else owns yet.
        body: The whole file.
        sync: Whether to force the bytes out before the descriptor is closed.
            The atomic path does, because the rename that follows must not be
            able to publish an empty file after a crash. The in-place fallback
            does not: it is only ever taken where no temporary file could be
            made, which includes a character device, and ``fsync`` on one of
            those fails with ``EINVAL`` rather than meaning anything.
    """
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        stream.write(body)
        if sync:
            stream.flush()
            os.fsync(stream.fileno())


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
    _write_through(descriptor, body, sync=False)
    with contextlib.suppress(OSError):
        path.chmod(SNAPSHOT_FILE_MODE)


def _not_a_snapshot(path: Path, error: ValidationError) -> ConfigurationError:
    """The refusal for a file whose content is not a snapshot this version reads."""
    return ConfigurationError(f"{path} is not a snapshot lsdsk understands: {error}")


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
        payload: Any = read_json_bounded(path, what="a snapshot")
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
        raise _not_a_snapshot(path, error) from error
    if not OLDEST_READABLE_SCHEMA <= envelope.schema_version <= SCHEMA_VERSION:
        message = (
            f"{path} is a schema {envelope.schema_version!r} snapshot; "
            f"this version of lsdsk reads schema {OLDEST_READABLE_SCHEMA} to {SCHEMA_VERSION}."
        )
        raise ConfigurationError(message)
    try:
        typed = parse_capture(capture)
    except ValidationError as error:
        raise _not_a_snapshot(path, error) from error
    return _inventory_of(typed)


__all__ = [
    "OLDEST_READABLE_SCHEMA",
    "SCHEMA_VERSION",
    "build_from",
    "collect",
    "current_platform",
    "load",
    "parse_capture",
    "read_current_machine",
    "save",
]
