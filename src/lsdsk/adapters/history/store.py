"""Where counter history is kept on disk, and what keeps that file trustworthy.

A capture can always be retaken from the hardware.  History cannot.  The drive
holds the running total and has never held the past, so once a history file is
lost or truncated the record of *when* the damage happened is gone for good and
no amount of re-reading brings it back.  That asymmetry drives three decisions
here: the replacement is atomic, the file is owner-only, and each drive's series
is capped.

The cap is per drive, which is the honest way to state it.  A drive that is
removed keeps its series, because a drive absent from one reading is far more
often a cable, an enclosure powered down or a controller reset than a disposal,
and discarding the history on that evidence would throw away the only copy of
the past for a drive that comes back in an hour.  So the file grows with the
number of distinct drives the machine has ever seen, which on real hardware is
small and rises only when disks are swapped.  A full series costs about 227 KB,
so the 64 MB read bound is reached at roughly 280 drives; past that the store
would be refused as oversized.  That is far beyond any real machine, but it is
a ceiling rather than the unbounded growth "capped per drive" might suggest.

The file lives in the platform's STATE directory rather than beside the
configuration.  Configuration is written by a human and is worth copying between
machines; this is machine-local measurement, and a config sync that carried it
would splice one machine's drives onto another's.

System Role:
    Adapter-layer persistence.  Owns paths, file format and durability; every
    rule about what a sample means lives in ``lsdsk.domain.history``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from ...domain.errors import ConfigurationError
from ...domain.history import DiskSeries, History, Sample
from ..textfile import read_text_bounded

HISTORY_SCHEMA_VERSION = 1

# The store carries every drive's serial number, exactly as a snapshot does, and
# is written by a privileged run. Sharing it is a deliberate act, not a default.
HISTORY_FILE_MODE = 0o600

# Sampling hourly for a decade would otherwise reach six figures of samples. The
# cap bounds the file; `thin` decides what a trim gives up.
MAX_SAMPLES_PER_DRIVE = 512

# A rate is errors per hour, so it is a float, so every stored magnitude has to
# survive conversion to one. A history store is a file the user points
# --history-file at, which makes its numbers input rather than measurement, and
# an hour count past the float ceiling raised OverflowError from inside the
# domain: `trend`, `health` and `findings` all died with a traceback instead of
# this tool's own "that is not a history store" refusal. The ceiling is about
# 1.8e308; the widest figure any decoder here can produce is a 128-bit NVMe
# field scaled by the data-unit size, around 1e44. 1e300 sits far above anything
# real and far below the limit, so it rejects only files that would crash.
MAX_STORED_MAGNITUDE = 10**300

#: Read once from the class rather than per sample; see the validator below.
_SAMPLE_FIELD_NAMES = tuple(field.name for field in dataclasses.fields(Sample))

# Where a root-run store belongs. The useful runs of this tool are all root on a
# server, and what it records is a property of the machine's hardware rather than
# of whoever happened to type the command, so a per-user directory would scatter
# one machine's history across several homes. Non-root keeps the per-user path,
# because a user cannot write here and would otherwise fail on every run.
SYSTEM_STORE_DIR = Path("/var/lib/lsdsk")

_VENDOR = "bitranox"
_APP = "lsdsk"
_FILENAME = "history.json"


class HistoryFile(BaseModel):
    """The on-disk shape, and the only place a stored file is trusted.

    The domain dataclasses are serialised directly rather than mirrored into a
    parallel set of models, which is this project's usual one-parse-in,
    one-dump-out arrangement.

    Attributes:
        schema_version: Format version. The wire key is ``schema``.
        hostname: The machine these samples were taken on.
        series: One series per drive.

    Example:
        >>> HistoryFile(hostname="box").schema_version
        1
    """

    model_config = {"populate_by_name": True}

    schema_version: int = Field(default=HISTORY_SCHEMA_VERSION, alias="schema")
    hostname: str
    series: tuple[DiskSeries, ...] = ()

    @field_validator("series")
    @classmethod
    def _magnitudes_must_fit_a_float(cls, series: tuple[DiskSeries, ...]) -> tuple[DiskSeries, ...]:
        """Refuse a stored number no rate arithmetic could survive.

        The check belongs here rather than in the domain because this is the
        trust boundary the file crosses; the domain is then free to divide
        without asking where its numbers came from.
        """
        # Read once because the field names belong to the class, not to a
        # sample. Not for speed: measured over five runs each, hoisting the
        # dataclasses.fields() call out of the loop is worth nothing (8.4 ms
        # against 8.5 ms per 10k samples), because it already returns a cached
        # tuple. The walk itself is the cost, about 19 ms to load a 20-drive
        # store at the sample cap, which is a once-per-run price worth paying
        # to keep a crash out of the domain.
        names = _SAMPLE_FIELD_NAMES
        for drive in series:
            for sample in drive.samples:
                for name in names:
                    value = getattr(sample, name)
                    if isinstance(value, int) and abs(value) > MAX_STORED_MAGNITUDE:
                        message = (
                            f"drive {drive.identity!r} has a {name} beyond "
                            f"{float(MAX_STORED_MAGNITUDE):.0e}, the largest figure this store holds"
                        )
                        raise ValueError(message)
        return series


def running_as_root() -> bool:
    """Whether this process can write the system-wide store.

    Windows has no euid, and its per-user path is already machine-appropriate,
    so it never takes the system branch.

    Example:
        >>> isinstance(running_as_root(), bool)
        True
    """
    getuid = getattr(os, "geteuid", None)
    return getuid is not None and getuid() == 0


def default_history_path() -> Path:
    """Where history lives when the configuration does not say otherwise.

    Returns:
        ``/var/lib/lsdsk/history.json`` for a root run on a POSIX host, and the
        per-user state file otherwise.

    Example:
        >>> default_history_path().name
        'history.json'
    """
    if sys.platform != "win32" and running_as_root():
        return SYSTEM_STORE_DIR / _FILENAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / _VENDOR / _APP / _FILENAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _VENDOR / _APP / _FILENAME
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / _APP / _FILENAME


def load_history(path: Path, *, hostname: str) -> History:
    """Read the store, or start an empty one.

    Args:
        path: The store file.
        hostname: The machine being read now.

    Returns:
        What has been recorded for this machine.

    Raises:
        ConfigurationError: If the file is unreadable, malformed, written by a
            newer lsdsk, or belongs to a different machine.

    Example:
        A directory the test owns, so the missing file is missing because this
        example says so. A fixed path like ``/nonexistent`` is not absent
        everywhere: on a Debian or Ubuntu box it is the ``nobody`` account's
        home, mode 0700, so the stat raises rather than answering no.

        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     load_history(Path(directory) / "history.json", hostname="box").series
        ()
    """
    if not path.exists():
        return History(hostname=hostname)

    try:
        payload: Any = json.loads(read_text_bounded(path, what="a history store"))
    # Not only JSONDecodeError: an integer literal past CPython's
    # digit limit raises a bare ValueError, and deeply nested JSON
    # exhausts the C stack with RecursionError. Both used to escape as a
    # traceback under the wrong exit code, when the honest answer is the
    # same refusal any other malformed file gets.
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        message = f"Could not read the history store at {path}: {error}"
        raise ConfigurationError(message) from error

    try:
        stored = HistoryFile.model_validate(payload)
    except ValidationError as error:
        message = f"{path} is not a history store lsdsk understands: {error}"
        raise ConfigurationError(message) from error

    if stored.schema_version != HISTORY_SCHEMA_VERSION:
        message = (
            f"{path} is a schema {stored.schema_version!r} history store; "
            f"this version of lsdsk reads schema {HISTORY_SCHEMA_VERSION}."
        )
        raise ConfigurationError(message)

    if stored.hostname != hostname:
        # Serial numbers are only unique in practice, and a virtual machine will
        # hand out a synthetic one that its neighbours share. Merging two
        # machines' stores would splice unrelated drives onto one series.
        message = (
            f"{path} holds history for {stored.hostname!r}, not for {hostname!r}. "
            "Point --history-file somewhere else rather than mixing two machines."
        )
        raise ConfigurationError(message)

    return History(hostname=stored.hostname, series=stored.series)


def save_history(history: History, path: Path) -> None:
    """Replace the store atomically.

    The new content is written to a temporary file in the same directory and
    then renamed over the old one, so an interrupted or failed write leaves the
    previous history exactly as it was. A plain truncate-and-write would destroy
    an accumulated record that cannot be rebuilt from the hardware.

    Args:
        history: What to store.
        path: The store file.

    Raises:
        OSError: If the directory cannot be created or the file cannot be
            replaced. The previous store is untouched in that case.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = HistoryFile(schema=HISTORY_SCHEMA_VERSION, hostname=history.hostname, series=history.series)
    body = stored.model_dump_json(indent=2, by_alias=True)

    handle, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp already creates at 0600; set it explicitly so the guarantee
        # does not rest on that, and so a umask cannot widen it.
        with contextlib.suppress(OSError):
            temporary.chmod(HISTORY_FILE_MODE)
        temporary.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def read_history(*, hostname: str, path: Path | None = None) -> History:
    """Read the store at the configured location.

    Args:
        hostname: The machine being read now.
        path: Override the default location.

    Returns:
        What has been recorded for this machine.
    """
    return load_history(path or default_history_path(), hostname=hostname)


def write_history(history: History, *, path: Path | None = None) -> None:
    """Replace the store at the configured location.

    Args:
        history: What to store.
        path: Override the default location.
    """
    save_history(history, path or default_history_path())


__all__ = [
    "HISTORY_FILE_MODE",
    "HISTORY_SCHEMA_VERSION",
    "MAX_SAMPLES_PER_DRIVE",
    "SYSTEM_STORE_DIR",
    "HistoryFile",
    "default_history_path",
    "load_history",
    "read_history",
    "running_as_root",
    "save_history",
    "write_history",
]
