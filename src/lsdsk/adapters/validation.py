"""Pydantic's refusal as this tool's own sentences.

Every JSON-backed store here is pydantic-validated on the way in, and every one
of them can meet a file somebody edited by hand or a write that was cut short.
What such a caller needs is which field and why. What ``str(ValidationError)``
gives them is a developer's document, so the two stores that have this problem
share one answer to it rather than each deciding again.

Contents:
    * :func:`what_is_wrong_with_it` - one ``<field>: <reason>`` line per problem
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import ValidationError

__all__ = ["what_is_wrong_with_it"]


def what_is_wrong_with_it(error: ValidationError) -> str:
    """Pydantic's report as this tool's own sentences, one per problem.

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
        One indented ``<field>: <reason>`` line per problem.

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
    """
    lines: list[str] = []
    for problem in error.errors():
        where = ".".join(str(part) for part in problem["loc"]) or "the file"
        lines.append(f"  {where}: {problem['msg']}")
    return "\n".join(lines)
