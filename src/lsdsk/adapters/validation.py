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

Contents:
    * :func:`what_is_wrong_with_it` - one ``<field>: <reason>`` line per problem
    * :data:`MAX_LISTED_PROBLEMS` - how many of them a refusal lists
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..domain.text import visible_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import ValidationError

__all__ = ["MAX_LISTED_PROBLEMS", "what_is_wrong_with_it"]

#: How many problems a refusal lists before counting the rest. A handful is
#: what a hand-edited file produces; a file that fails in every entry fails the
#: same way in all of them, so the first twenty say what the rest would.
#: Measured before the cap: 100,000 crafted entries in a 13 MB capture made
#: 12 MB of stderr and a 16 MB JSON envelope.
MAX_LISTED_PROBLEMS = 20

#: The longest a reason is quoted. Pydantic writes its own, but a validator's
#: reason can quote the value it refused, which the file chose.
_REASON_LIMIT = 200


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
