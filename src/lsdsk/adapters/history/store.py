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
small and rises only when disks are swapped.  A full series costs about 210 KB
with every counter sixteen digits long, so the 64 MB read bound is reached at
roughly 300 such drives.  The writer refuses a store past that bound rather than
write one its own reader would refuse, so a run that would cross it fails
loudly and leaves the previous store as it was.  That is far beyond any real
machine, but it is a ceiling rather than the unbounded growth "capped per
drive" might suggest.

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
import errno
import json
import os
import sys
import time
from collections.abc import Generator, Mapping, Sequence, Sized
from pathlib import Path
from typing import Annotated, Any, NamedTuple, cast

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from ...domain.errors import ConfigurationError, MissingFileError
from ...domain.history import DiskSeries, History, Sample, merge_duplicate_series, thin
from ...domain.text import MAX_DEVICE_TEXT, visible_text
from ..atomicfile import replace_atomically
from ..textfile import MAX_INPUT_BYTES, fits_a_bounded_read, read_json_bounded
from ..validation import BOUNDED, MAX_ENTRIES, what_is_wrong_with_it

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
_SAMPLE_FIELD_NAMES = tuple(Sample.model_fields)

# Where a root-run store belongs. The useful runs of this tool are all root on a
# server, and what it records is a property of the machine's hardware rather than
# of whoever happened to type the command, so a per-user directory would scatter
# one machine's history across several homes. Non-root keeps the per-user path,
# because a user cannot write here and would otherwise fail on every run.
SYSTEM_STORE_DIR = Path("/var/lib/lsdsk")

_VENDOR = "bitranox"
_APP = "lsdsk"
_FILENAME = "history.json"


def _samples_within_bounds(value: object) -> object:
    """Refuse a series holding more samples than one collection may.

    Reads the raw series - a mapping from the file, or a ``DiskSeries`` a caller
    is about to write - because it runs before the series are validated, which
    is what keeps a million-sample series from being built only to be thinned.

    Args:
        value: The raw ``series`` field.

    Returns:
        ``value`` unchanged. Anything that is not a list of series passes
        through to the field's own validation, which refuses it by type.

    Raises:
        PydanticCustomError: If a series holds more than
            :data:`~lsdsk.adapters.validation.MAX_ENTRIES` samples.
    """
    _refuse_an_oversized_series(value)
    return value


def _refuse_an_oversized_series(value: object) -> None:
    """The walk :func:`_samples_within_bounds` makes, over whatever shape it was handed."""
    if not isinstance(value, list | tuple):
        return
    for entry in cast("Sequence[object]", value):
        if isinstance(entry, Mapping):
            fields = cast("Mapping[str, object]", entry)
            identity, samples = fields.get("identity"), fields.get("samples")
        else:
            identity, samples = getattr(entry, "identity", None), getattr(entry, "samples", None)
        if isinstance(samples, list | tuple) and len(cast("Sized", samples)) > MAX_ENTRIES:
            raise PydanticCustomError(
                "too_many_entries",
                "the series '{identity}' holds {count} samples, more than the {limit} lsdsk reads in one place",
                {"identity": str(identity), "count": len(cast("Sized", samples)), "limit": MAX_ENTRIES},
            )


class HistoryFile(BaseModel):
    """The on-disk shape, and the only place a stored file is trusted.

    The domain models are serialised directly rather than mirrored into a
    parallel set of models, which is this project's usual one-parse-in,
    one-dump-out arrangement.

    Attributes:
        schema_version: Format version. The wire key is ``schema``.
        hostname: The machine these samples were taken on, at most
            :data:`~lsdsk.domain.text.MAX_DEVICE_TEXT` characters like every
            other string in the store. Bounded but not cleaned: a store for
            another machine is refused quoting the name as the file spelled it.
        series: One series per drive. At most
            :data:`~lsdsk.adapters.validation.MAX_ENTRIES` of them, each of at
            most that many samples, both counted before anything in them is
            validated. The samples are bounded HERE rather than on the domain's
            own ``DiskSeries``, because the domain cannot know the file's limit
            and ``record`` builds series the configuration sizes.

    Example:
        >>> HistoryFile(hostname="box").schema_version
        1
    """

    model_config = {"populate_by_name": True}

    schema_version: int = Field(default=HISTORY_SCHEMA_VERSION, alias="schema")
    hostname: Annotated[str, Field(max_length=MAX_DEVICE_TEXT)]
    series: Annotated[tuple[DiskSeries, ...], BOUNDED, BeforeValidator(_samples_within_bounds)] = ()

    @field_validator("series")
    @classmethod
    def _magnitudes_must_fit_a_float(cls, series: tuple[DiskSeries, ...]) -> tuple[DiskSeries, ...]:
        """Refuse a stored number no rate arithmetic could survive.

        The check belongs here rather than in the domain because this is the
        trust boundary the file crosses; the domain is then free to divide
        without asking where its numbers came from.
        """
        # Read once because the field names belong to the class, not to a
        # sample, and not for speed: the class holds them ready either way.
        # The walk itself is the cost, about 19 ms to load a 20-drive store at
        # the sample cap, which is a once-per-run price worth paying to keep a
        # crash out of the domain.
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

    Returns:
        Whether the system-wide store is writable by this process.

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


def load_history(path: Path, *, hostname: str, cap: int = MAX_SAMPLES_PER_DRIVE) -> History:
    """Read the store, or start an empty one.

    Each series is thinned to ``cap`` on the way in, exactly as ``record``
    thins it on the way out. The store is a file a user points
    ``--history-file`` at, so nothing guarantees ``record`` wrote it, and every
    judgement walks a drive's whole series once per counter: two series of
    50,000 samples made ``report`` take 6.6 s. Thinning is idempotent, so a
    series ``record`` wrote under the same cap is returned unchanged.

    Args:
        path: The store file.
        hostname: The machine being read now.
        cap: The most samples any one drive keeps, which is the configured
            ``max_samples_per_drive``.

    Returns:
        What has been recorded for this machine, each series at most ``cap``
        samples long.

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
    try:
        payload: Any = read_json_bounded(path, what="a history store")
    # Asked of the READ rather than of path.exists(), which answers False for a
    # store this process may not look at exactly as it does for one that was
    # never written: the OSError is swallowed inside it. Read as absent, an
    # unreadable store makes every rate verdict degrade to "first sample" while
    # the page says nothing has been recorded on this machine yet, which is the
    # opposite of what happened. Reachable without anything unusual - a root
    # timer under umask 077 leaves the state directory 0700.
    except MissingFileError:
        return History(hostname=hostname)
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
        # The same refusal the snapshot loader gives, for the same reason: the
        # raw report names an internal model, quotes a slice of the caller's own
        # file back at them and carries the pinned pydantic version's URL. The
        # whole of it stays reachable through --traceback.
        message = f"{path} is not a history store lsdsk understands:\n{what_is_wrong_with_it(error)}"
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
        # machines' stores would splice unrelated drives onto one series. The
        # stored name is quoted as the file wrote it, so it is quoted inert and
        # cut short: repr kept its escapes harmless but not its length.
        message = (
            f"{path} holds history for '{visible_text(stored.hostname)}', not for '{visible_text(hostname)}'. "
            "Point --history-file somewhere else rather than mixing two machines."
        )
        raise ConfigurationError(message)

    series = merge_duplicate_series(stored.series)
    return History(hostname=stored.hostname, series=tuple(_capped(one, cap) for one in series))


def _capped(series: DiskSeries, cap: int) -> DiskSeries:
    """The series as ``record`` would keep it under ``cap``."""
    if len(series.samples) <= cap:
        return series
    return series.with_changes(samples=thin(series.samples, cap))


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
            replaced, or the store would be larger than :func:`load_history`
            reads (``errno.EFBIG``). The previous store is untouched in every
            case.

            A history the reader's own model refuses - more series or samples
            than :data:`~lsdsk.adapters.validation.MAX_ENTRIES`, a figure past
            the stored magnitude - is refused the same way (``errno.EINVAL``)
            rather than as a ``ValidationError``, which no caller catches.
    """
    try:
        stored = HistoryFile(schema=HISTORY_SCHEMA_VERSION, hostname=history.hostname, series=history.series)
    except ValidationError as error:
        message = (
            "the history would not be a store the history reader accepts, so nothing was written "
            f"and the previous store is kept:\n{what_is_wrong_with_it(error)}"
        )
        raise OSError(errno.EINVAL, message, str(path)) from error
    # Compact rather than indented: indentation is a third of a full series'
    # size and nobody reads this file by eye.
    body = stored.model_dump_json(by_alias=True)
    _refuse_what_the_reader_would_refuse(body, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    replace_atomically(path, body, mode=HISTORY_FILE_MODE)


def _refuse_what_the_reader_would_refuse(body: str, path: Path) -> None:
    """Refuse a store the reader would refuse, before anything is written.

    The reader bounds a history store like any file from outside the tool, and
    a writer with no bound of its own once produced a file past it: every later
    run then ignored the store and recorded nothing, while ``record`` exited 0.
    Raised as an ``OSError`` because that is what every caller already reports
    as a write that failed; the previous store stays readable and is kept.

    Args:
        body: The whole file, as it would be written.
        path: The store, for the message.

    Raises:
        OSError: With ``errno.EFBIG``, if ``body`` is over the read limit.
    """
    if not fits_a_bounded_read(body):
        size = len(body.encode("utf-8"))
        message = (
            f"the history store would be {size / 1024 / 1024:.1f} MB, larger than the history reader accepts "
            f"({MAX_INPUT_BYTES // 1024 // 1024} MB), so nothing was written and the previous store is kept"
        )
        raise OSError(errno.EFBIG, message, str(path))


def read_history(*, hostname: str, path: Path | None = None, cap: int = MAX_SAMPLES_PER_DRIVE) -> History:
    """Read the store at the configured location.

    Args:
        hostname: The machine being read now.
        path: Override the default location.
        cap: The most samples any one drive keeps.

    Returns:
        What has been recorded for this machine.
    """
    return load_history(path or default_history_path(), hostname=hostname, cap=cap)


def write_history(history: History, *, path: Path | None = None) -> None:
    """Replace the store at the configured location.

    Args:
        history: What to store.
        path: Override the default location.
    """
    save_history(history, path or default_history_path())


class HistoryRead(NamedTuple):
    """What the store held, and whether this run may write over it.

    The two are separate answers. An unreadable store still yields an empty
    history so the hardware is diagnosed anyway, but it must never be treated as
    "there was nothing here", because that is indistinguishable from an empty
    store right up until the moment it is overwritten.

    Attributes:
        history: What has been recorded, empty when there is nothing usable.
        writable: Whether this run may replace the file. False only when a store
            is present and could not be read.
        refusal: Why it may not be replaced, as the sentence the reader produced.
            ``None`` when nothing refused. Carried rather than left on stderr
            because ``record --format json`` reports this cause apart from the
            others, and a caller parsing stdout cannot see a warning.
    """

    history: History
    writable: bool
    refusal: str | None = None


#: How long a run waits for another to finish writing the store before giving
#: up. A write takes milliseconds; a holder this slow has hung, and waiting on it
#: forever would hang a timer behind it.
LOCK_WAIT_SECONDS = 30.0

#: How often a waiting run asks again.
_LOCK_POLL_SECONDS = 0.05


def _try_lock(descriptor: int) -> bool:
    """Take the exclusive lock on ``descriptor`` without waiting; ``False`` when another run holds it."""
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415 - Windows-only module

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl  # noqa: PLC0415 - POSIX-only module

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


@contextlib.contextmanager
def history_lock(path: Path, *, wait: float = LOCK_WAIT_SECONDS) -> Generator[None]:
    """Hold the store exclusively from reading it to writing it back.

    Two runs that overlap - a timer's ``record`` and an interactive ``lsdsk`` -
    each read the store, add a reading and write the whole of it back, so the
    one that wrote second replaced the other's sample with the copy it had read
    before that sample existed. A lock on a file beside the store serialises
    them. The operating system releases it when its holder exits however that
    happens, so a crashed run leaves no lock behind, only an empty file.

    Args:
        path: The store. The lock is ``.<name>.lock`` in the same directory.
        wait: How long to wait for another run before giving up.

    Raises:
        OSError: If the lock file cannot be created (``PermissionError`` for a
            refusal), or another run held the store for longer than ``wait``
            (``errno.EAGAIN``).

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as directory, history_lock(Path(directory) / "h.json"):
        ...     pass
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.with_name(f".{path.name}.lock"), os.O_RDWR | os.O_CREAT, HISTORY_FILE_MODE)
    try:
        deadline = time.monotonic() + wait
        while not _try_lock(descriptor):
            if time.monotonic() >= deadline:
                message = f"another lsdsk run has held the counter store for over {wait:g} seconds"
                raise OSError(errno.EAGAIN, message, str(path))
            time.sleep(_LOCK_POLL_SECONDS)
        yield
    finally:
        # Closing the descriptor releases the lock on every platform.
        os.close(descriptor)


__all__ = [
    "HISTORY_FILE_MODE",
    "HISTORY_SCHEMA_VERSION",
    "LOCK_WAIT_SECONDS",
    "MAX_SAMPLES_PER_DRIVE",
    "SYSTEM_STORE_DIR",
    "HistoryFile",
    "HistoryRead",
    "default_history_path",
    "history_lock",
    "load_history",
    "read_history",
    "running_as_root",
    "save_history",
    "write_history",
]
