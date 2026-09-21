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

from typing import Annotated

from pydantic import BaseModel, Field

from ...domain.enums import Platform

#: The longest a piece of text a DEVICE chose may be. Every one of these is an
#: identifier, a name or a rate as the platform published it: four hex
#: characters from sysfs, a model string, a driver name, `8.0 GT/s PCIe`. The
#: bound is far above anything real and exists because the file ceiling is not a
#: bound on what ONE field can do downstream - an identifier round-trips through
#: an integer parse, a hex re-format and a per-character generator, which
#: measured a 12 to 13x memory multiplier, so a single field inside the 64 MB
#: file limit could reach roughly 800 MB. It does not cover the base64 payloads
#: beside them: a 4096-byte IDENTIFY page encodes to more than this, and those
#: are decoded at fixed offsets rather than walked per character.
MAX_DEVICE_TEXT = 4096

#: Text a device published, bounded. Optional fields spell it `DeviceText | None`.
DeviceText = Annotated[str, Field(max_length=MAX_DEVICE_TEXT)]


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

    Attributes:
        schema_version: The capture format version. Aliased because Pydantic's
            ``BaseModel`` already has a ``schema`` attribute, and the wire key
            is the contract.
        hostname: The machine the capture was taken on.
        kernel: The kernel or Windows build it was running.
        captured_at: When it was taken, ISO 8601. Schema 1 captures predate it.
    """

    schema_version: int = Field(alias="schema")
    hostname: str
    kernel: str
    captured_at: str | None = None


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


__all__ = ["MAX_DEVICE_TEXT", "CaptureEnvelope", "CaptureHeader", "CaptureModel", "DeviceText"]
