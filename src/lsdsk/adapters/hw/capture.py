"""What every capture carries, whichever platform wrote it.

A capture is the raw reading a platform reader wrote, not a rendered result, and
it is typed before anything maps it: a live run and a replay both pass through
:func:`~lsdsk.adapters.hw.snapshot.parse_capture`. The platform packages type the
rest of it (``linux/capture.py``, ``windows/capture.py``); this module holds only
what both share, so neither platform imports the other and the snapshot module
can read a file's platform before choosing which model to parse it with.

System Role:
    Adapter layer, the part of the parse step both platforms share.
"""

from __future__ import annotations

from typing import Annotated, Self, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

from ...domain.enums import Platform
from ...domain.fragmentation import FREE_SPACE_BUCKETS
from ...domain.text import MAX_DEVICE_TEXT
from ..validation import BOUNDED

_Key = TypeVar("_Key")
_Value = TypeVar("_Value")

#: The largest page a reader stores, decoded. A VPD page's two-byte length field
#: allows 65,539 bytes, so the ceiling cannot sit at 64 KiB; it is the megabyte
#: the Linux reader already stops a binary sysfs attribute at, so nothing a live
#: read keeps is refused when that same capture is parsed. A page is decoded at
#: fixed offsets, once per drive, so its length is not multiplied downstream.
MAX_PAYLOAD_BYTES = 1024 * 1024

#: :data:`MAX_PAYLOAD_BYTES` in the base64 text a capture actually stores a
#: page as: four characters per three raw bytes, rounded up to a whole group.
#: It is not an EXACT twin of the raw ceiling, because base64 only grows its
#: output once a full three-byte group completes - a raw payload one or two
#: bytes past :data:`MAX_PAYLOAD_BYTES` encodes to this same length, so this
#: bound admits up to two raw bytes of quantisation slack past the stated
#: ceiling. A third byte over is what first exceeds it.
MAX_ENCODED_PAYLOAD = 4 * -(-MAX_PAYLOAD_BYTES // 3)

#: Text a capture carries - a name, identifier, rate, path or a reader's refusal -
#: bounded. Optional fields spell it `DeviceText | None`, and a mapping bounds its
#: KEY with it as well as its value, since a key can carry the same payload.
#: The bound is :data:`lsdsk.domain.text.MAX_DEVICE_TEXT`, where the reason for
#: its size is recorded; it is the domain's so a capture and the domain models
#: it becomes cannot disagree about it. It does not cover the base64 payloads
#: beside this text: a 4096-byte IDENTIFY page encodes to more than this, so
#: those carry their own bound, :data:`EncodedPayload`.
DeviceText = Annotated[str, Field(max_length=MAX_DEVICE_TEXT)]

#: A binary page as base64, bounded. Decoded at fixed offsets rather than walked
#: per character, which is why it may be longer than :data:`DeviceText`.
EncodedPayload = Annotated[str, Field(max_length=MAX_ENCODED_PAYLOAD)]

#: A keyed section of a capture - PCI devices, block devices, a sysfs class -
#: bounded in ENTRIES as well as by the file's bytes, and checked before any
#: entry is validated. :data:`lsdsk.adapters.validation.MAX_ENTRIES` records why.
EntryMap = Annotated[dict[_Key, _Value], BOUNDED]

#: A list inside a capture - a device's children, a disk's ancestors - bounded
#: the same way, since one entry's list can carry as much as a whole section.
Entries = Annotated[tuple[_Value, ...], BOUNDED]


class CaptureModel(BaseModel, frozen=True, extra="ignore"):
    """Base for every part of a capture.

    Frozen, because a capture records what was read and nothing downstream may
    change it. Keys a model does not name are ignored rather than refused: a
    capture carries more than the builders read (VPD pages, the error text of a
    refused passthrough, context kept for a bug report), and a snapshot written
    by a newer reader has to keep loading. A key a model DOES name must hold the
    type it names, or the capture is refused.

    Declared as CLASS KEYWORDS rather than a ``model_config`` dict, the same way
    :class:`lsdsk.domain.base.DomainModel` is and for the same reason: pyright
    reads only the keyword, so with the dict it holds every subclass unhashable,
    and a subclass that forgets ``frozen`` is an error instead of a quietly
    mutable class. Nothing here is put in a set today, which is exactly why the
    difference was invisible.
    """


class CaptureHeader(CaptureModel, frozen=True):
    """The keys every capture has, whichever platform wrote it.

    Every field without a default is REQUIRED, and that is the guard. Given
    defaults instead, an empty object validated: replaying any JSON file at all
    reported a machine called "unknown" with no disks and nothing wrong, exit 0,
    and blamed the absent readings on privilege. A tool whose first rule is never
    to report what it did not measure has to refuse the file rather than describe
    it.

    It types only what a builder reads. Both readers also write ``kernel`` - the
    kernel or Windows build the capture was taken on - and it stays in every
    capture for the person reading one, since what a Linux capture can hold
    depends on the kernel version; nothing maps it, so it is one of the keys
    :class:`CaptureModel` ignores, and a capture without it is not refused for
    lacking a value no output is built from.

    Attributes:
        schema_version: The capture format version. Aliased because Pydantic's
            ``BaseModel`` already has a ``schema`` attribute, and the wire key
            is the contract.
        hostname: The machine the capture was taken on.
        captured_at: When it was taken, ISO 8601. Schema 1 captures predate it.
    """

    schema_version: int = Field(alias="schema")
    hostname: DeviceText
    captured_at: DeviceText | None = None


class CaptureEnvelope(CaptureHeader, frozen=True):
    """The header of a snapshot of either platform.

    Read on its own before the platform model is chosen, so a file that is not a
    snapshot at all, or is one from a schema this version cannot read, is refused
    with that reason rather than with whatever the platform model trips over
    first.

    Attributes:
        platform: The platform the capture was taken on, which picks its model.

    Example:
        >>> CaptureEnvelope.model_validate(
        ...     {"schema": 1, "platform": "linux", "hostname": "example", "kernel": "6.1.0"}
        ... ).platform
        <Platform.LINUX: 'linux'>
    """

    platform: Platform


#: The largest value an unsigned 32-bit field holds, which bounds every capture figure the
#: platform publishes in one (an AHCI register, a uid, a DEVPKEY UINT32).
UINT32_MAX = 2**32 - 1
#: The largest value an unsigned 64-bit field holds, which bounds every byte figure a capture
#: records - a ZFS pool's size, a filesystem's statvfs counts, a Windows volume's free bytes, a
#: free-extent total - so a reader that refuses past it and the model that refuses past it agree.
UINT64_MAX = 2**64 - 1
_FreeBytes = Annotated[int, Field(ge=0, le=UINT64_MAX)]


class FreeExtentsEntry(CaptureModel, frozen=True):
    """One filesystem's free extents, as a bounded summary, or why they were not read.

    Attributes:
        count: How many free runs the walk met.
        total: Their total size in bytes.
        largest: The longest run in bytes.
        histogram: Free bytes per power-of-two bucket, empty or exactly
            ``FREE_SPACE_BUCKETS`` long.
        error: Why the walk was refused or stopped; the figures are then ``None``.

    The figures are refused unless they agree the way a walk writes them -
    count, total and largest together or not at all, none beside an error,
    a histogram that sums to the total, and a total that the counted extents,
    each at least a byte and at most the largest, could hold - so an edited
    replay is refused as a bad file rather than drawn as a reading.
    """

    count: int | None = Field(default=None, ge=0, le=UINT64_MAX)
    total: int | None = Field(default=None, ge=0, le=UINT64_MAX)
    largest: int | None = Field(default=None, ge=0, le=UINT64_MAX)
    histogram: Entries[_FreeBytes] = ()
    error: DeviceText | None = None

    @field_validator("histogram")
    @classmethod
    def _one_bucket_per_score(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if value and len(value) != FREE_SPACE_BUCKETS:
            message = f"a free-space histogram has {FREE_SPACE_BUCKETS} buckets, not {len(value)}"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _figures_agree(self) -> Self:
        figures = (self.count, self.total, self.largest)
        if self.error is not None and (any(figure is not None for figure in figures) or self.histogram):
            message = "a refused reading carries no figures"
            raise ValueError(message)
        if self.count is None or self.total is None or self.largest is None:
            _refuse_partial_figures(figures, histogram=self.histogram)
            return self
        _refuse_disagreeing_figures(self.count, self.total, self.largest)
        if self.histogram and sum(self.histogram) != self.total:
            message = f"the histogram sums to {sum(self.histogram)}, not the total {self.total}"
            raise ValueError(message)
        return self


def _refuse_partial_figures(figures: tuple[int | None, ...], *, histogram: tuple[int, ...]) -> None:
    """Refuse a summary carrying some of its figures, or a histogram with none of them."""
    if any(figure is not None for figure in figures):
        message = "count, total and largest are read together, not one without the others"
        raise ValueError(message)
    if histogram:
        message = "a histogram without the figures it sums"
        raise ValueError(message)


def _refuse_disagreeing_figures(count: int, total: int, largest: int) -> None:
    """Refuse a count, total and largest no walk could have written: each extent is 1 to ``largest`` bytes."""
    if largest > total:
        message = f"the largest extent {largest} is more than the total {total}"
        raise ValueError(message)
    if total > count * largest:
        message = f"{count} extents of at most {largest} bytes cannot hold {total}"
        raise ValueError(message)
    if count > total:
        message = f"{count} extents cannot fit in {total} bytes"
        raise ValueError(message)


__all__ = [
    "MAX_DEVICE_TEXT",
    "MAX_ENCODED_PAYLOAD",
    "MAX_PAYLOAD_BYTES",
    "UINT32_MAX",
    "UINT64_MAX",
    "CaptureEnvelope",
    "CaptureHeader",
    "CaptureModel",
    "DeviceText",
    "EncodedPayload",
    "Entries",
    "EntryMap",
    "FreeExtentsEntry",
]
