"""Pydantic's refusal as this tool's own sentences.

Every JSON-backed store here is pydantic-validated on the way in, and every one
of them can meet a file somebody edited by hand or a write that was cut short.
What such a caller needs is which field and why. What ``str(ValidationError)``
gives them is a developer's document, so the two stores that have this problem
share one answer to it rather than each deciding again.

The file being refused chose part of that answer: a location is built from the
file's own keys, so it is quoted through :func:`~lsdsk.domain.text.visible_text`
like any other untrusted text, and the file also chose how many problems there
are, so only the first :data:`MAX_LISTED_PROBLEMS` are listed.

Both stores also bound how many ENTRIES one collection in them may hold, for
the reason :data:`MAX_ENTRIES` records, and share that bound here for the same
reason they share the sentences.

Contents:
    * :func:`what_is_wrong_with_it` - one ``<field>: <reason>`` line per problem
    * :data:`MAX_LISTED_PROBLEMS` - how many of them a refusal lists
    * :data:`MAX_ENTRIES` - the most entries one collection in either file holds
    * :func:`at_most_entries` - the check, as a before-validator
    * :data:`BOUNDED` - that check, ready to put in an ``Annotated`` type
"""

from __future__ import annotations

from collections.abc import Sized
from typing import TYPE_CHECKING, Final

from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError

from ..domain.text import visible_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import ValidationError

__all__ = ["BOUNDED", "MAX_ENTRIES", "MAX_LISTED_PROBLEMS", "at_most_entries", "what_is_wrong_with_it"]

#: How many problems a refusal lists before counting the rest. A handful is
#: what a hand-edited file produces; a file that fails in every entry fails the
#: same way in all of them, so the first twenty say what the rest would.
#: Measured before the cap: 100,000 crafted entries in a 13 MB capture made
#: 12 MB of stderr and a 16 MB JSON envelope.
MAX_LISTED_PROBLEMS = 20

#: The longest a reason is quoted. Pydantic writes its own, but a validator's
#: reason can quote the value it refused, which the file chose.
_REASON_LIMIT = 200

#: The most entries one collection in a capture or a history store may hold.
#:
#: The file ceiling bounds BYTES, and what a parsed document costs is linear in
#: its ENTRY count with a large constant, because every entry becomes several
#: Python objects and then a model: measured, 200,000 empty PCI entries in a
#: 4 MB capture made ``findings`` take 7 s and 703 MB, and 4.5 million in 58.5 MB
#: took 156 s and 14.4 GB. So the count is bounded as well, before any entry is
#: validated.
#:
#: 65,536 is the whole function space of one PCI segment (256 buses x 32 devices
#: x 8 functions), which no collection a machine publishes approaches: the
#: largest in the committed captures is 95 PCI functions, and a storage server
#: with thousands of multipath LUNs or zvols stays an order of magnitude under
#: it. A history store's series are the drives a machine has ever seen, and a
#: series is thinned to a few hundred samples. At the bound, a collection costs
#: a second or two to VALIDATE rather than minutes.
#:
#: What it does not bound is the parse. The count is checked on the document
#: ``json.loads`` has already built, so every file inside
#: :data:`~lsdsk.adapters.textfile.MAX_INPUT_BYTES` is parsed whole first, however
#: many entries it holds: measured, a 67 MB capture of 5.7 million ``"k":{}``
#: entries cost 7 to 8 s and 1.8 GB of peak memory before this bound refused it
#: at 78.
MAX_ENTRIES: Final = 65_536


def at_most_entries(value: object) -> object:
    """Refuse a collection holding more than :data:`MAX_ENTRIES` entries.

    Run BEFORE the entries are validated, which is the point: pydantic's own
    ``max_length`` on a mapping counts after validating every entry, so the
    whole cost the bound exists to avoid is paid before it refuses.

    Args:
        value: The raw collection, as the file or the caller handed it.

    Returns:
        ``value`` unchanged. Anything that is not a sized collection passes
        through to the field's own validation, which refuses it by type.

    Raises:
        PydanticCustomError: If ``value`` holds more than :data:`MAX_ENTRIES`
            entries. It reaches the caller as one ``<field>: <reason>`` line.

    Example:
        >>> at_most_entries({"a": 1})
        {'a': 1}
        >>> at_most_entries([0] * (MAX_ENTRIES + 1))
        Traceback (most recent call last):
            ...
        pydantic_core._pydantic_core.PydanticCustomError: holds 65537 entries, more than ...
    """
    if isinstance(value, Sized) and not isinstance(value, str | bytes) and len(value) > MAX_ENTRIES:
        raise PydanticCustomError(
            "too_many_entries",
            "holds {count} entries, more than the {limit} lsdsk reads in one place",
            {"count": len(value), "limit": MAX_ENTRIES},
        )
    return value


#: :func:`at_most_entries` as the metadata of an ``Annotated`` collection type.
BOUNDED: Final = BeforeValidator(at_most_entries)


def what_is_wrong_with_it(error: ValidationError) -> str:
    r"""Pydantic's report as this tool's own sentences, one per problem.

    ``str(ValidationError)`` is a developer's document: it names the internal
    union it tried (``tagged-union[LinuxCapture,WindowsCapture]``), quotes a
    truncated slice of the caller's own file back at them, and invites them to a
    URL carrying the pinned pydantic version. Four such blocks for an empty
    object. What a caller needs is which field and why, which is what
    ``errors()`` carries as data; the full report stays reachable as the
    exception's ``__cause__``, which ``--traceback`` prints.

    Args:
        error: What a model refused.

    Returns:
        One indented ``<field>: <reason>`` line for each of the first
        :data:`MAX_LISTED_PROBLEMS` problems, and a line counting the rest.

    Examples:
        >>> from pydantic import BaseModel, ValidationError
        >>> class Thing(BaseModel):
        ...     count: int
        >>> try:
        ...     Thing(count="not a number")
        ... except ValidationError as refused:
        ...     print(what_is_wrong_with_it(refused))
        ... # doctest: +ELLIPSIS
          count: Input should be a valid integer...
        >>> class Table(BaseModel):
        ...     rows: dict[str, int]
        >>> try:
        ...     Table(rows={"row\x1b]0;x\x07": "no"})
        ... except ValidationError as refused:
        ...     print(what_is_wrong_with_it(refused))
        ... # doctest: +ELLIPSIS
          rows.row\x1b]0;x\x07: Input should be a valid integer...
    """
    problems = error.errors(include_url=False, include_context=False, include_input=False)
    lines = [
        f"  {_where(problem['loc'])}: {visible_text(problem['msg'], _REASON_LIMIT)}"
        for problem in problems[:MAX_LISTED_PROBLEMS]
    ]
    left_out = len(problems) - len(lines)
    if left_out:
        lines.append(f"  and {left_out} more")
    return "\n".join(lines)


def _where(location: Sequence[int | str]) -> str:
    r"""The dotted path to a problem, each part quoted as the untrusted text it is.

    Example:
        >>> _where(("pci", "0000:00:1f.2\n", "vendor"))
        'pci.0000:00:1f.2\\x0a.vendor'
        >>> _where(())
        'the file'
    """
    return ".".join(visible_text(str(part)) for part in location) or "the file"
