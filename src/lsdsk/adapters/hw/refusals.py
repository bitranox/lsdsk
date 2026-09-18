"""Turn the error text a reader recorded into domain values, for both platforms.

Both readers already write the operating system's own words when a device refuses
a reading: a passthrough behind a RAID driver, a register mapping some hosts deny
even to root, a device that cannot be opened without Administrator. What was
missing was the step after that - the text reached the capture and stopped there,
so a scan that had been refused half its readings was indistinguishable from a
complete one.

Shared rather than written twice because the two platforms record the same fact
under the same key names, and because the reading VOCABULARY is what a caller
reads: ``smart-data`` has to mean the same thing whichever reader produced it.
Each builder passes the labels explicitly rather than having them derived from
field names, so a field renamed for the wire cannot silently rename a reading a
monitoring check greps for.

System Role:
    Adapter layer, part of the pure mapping from a typed capture to the domain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.models import RefusedReading

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["refusals_of"]


def refusals_of(reported: Mapping[str, str | None]) -> tuple[RefusedReading, ...]:
    """Build one value per reading the machine refused.

    Args:
        reported: What each reading's refusal said, keyed by the reading's name
            as a caller sees it. ``None`` means the reading was not refused,
            which is the ordinary case for every key.

    Returns:
        One :class:`~lsdsk.domain.models.RefusedReading` per reading that carries
        a reason, in the order given. Empty when nothing was refused, which is
        what keeps a complete scan reporting complete.

    Example:
        >>> refusals_of({"smart-data": None, "identify": "[Errno 1] Operation not permitted"})
        (RefusedReading(reading='identify', reason='[Errno 1] Operation not permitted'),)
        >>> refusals_of({"smart-data": None})
        ()
    """
    return tuple(RefusedReading(reading=reading, reason=reason) for reading, reason in reported.items() if reason)
