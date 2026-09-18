"""The one frozen value-object base every domain model is built on.

The domain is values: a reading, a link, a finding, a threshold. Nothing in it is
edited after construction, so every model is frozen, and a field name nobody
declared is a typo rather than data, so every model refuses one. Both rules live
here once, rather than in fifteen ``model_config`` lines that could drift apart.

Validating at construction is the point of the base. A capture is typed on the
way in (``adapters/hw/capture.py``) and the builders that read it are pure
mappings, so a wrong-typed value arriving in a model is a defect in a mapping -
and this is where it surfaces, at the construction that made it, rather than
several layers later as an ``AttributeError`` in a renderer.

Coercion stays lax on purpose: a tuple field accepts the sequence a builder
hands it, and an ``int`` reaches a ``float`` field unremarked. What is refused is
a value of the wrong KIND and a field that does not exist.

System Role:
    Domain base. Imports pydantic and nothing of this project's own, so every
    layer may depend on it.

Example:
    >>> class Reading(DomainModel):
    ...     value: int = 0
    >>> Reading(value=3).model_dump()
    {'value': 3}
    >>> Reading(vlaue=3)
    Traceback (most recent call last):
        ...
    pydantic_core._pydantic_core.ValidationError: ...
    >>> Reading(value=3).value = 4
    Traceback (most recent call last):
        ...
    pydantic_core._pydantic_core.ValidationError: ...
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel

__all__ = ["DomainModel"]


class DomainModel(BaseModel, frozen=True, extra="forbid"):
    """A frozen, extra-refusing Pydantic model: the shape of every domain value.

    Declared as class keywords rather than a ``model_config`` dict because
    pyright reads only the keywords: with the dict it holds every model
    unhashable and refuses ``{disk}`` although pydantic hashes a frozen model
    perfectly well. It also turns the omission into an error - a model that
    leaves ``frozen=True`` off its own header is refused as a non-frozen class
    inheriting from a frozen one, so the rule cannot be forgotten quietly.
    """

    def with_changes(self, **changes: object) -> Self:
        """Return a copy of this value with ``changes`` applied.

        The frozen-value equivalent of an edit: a caller that knows one field
        late - a port count only the whole machine can answer, a severity a
        trend revises - says only what changed, and a field the model gains
        later travels along rather than being silently dropped by a rebuild
        that restates every other field.

        It goes through validation, which ``model_copy(update=...)`` does not:
        pydantic writes an unknown key there straight into the instance, so
        ``model_copy(update={"prots_used": 3})`` leaves ``ports_used`` alone,
        sets an attribute nothing reads, dumps nothing extra and raises
        nothing. Measured on pydantic 2.13.5. Here the typo is refused.

        Args:
            **changes: Field values to replace, by field name.

        Returns:
            A new instance of the same type with ``changes`` applied.

        Raises:
            pydantic.ValidationError: If a name is not a field of this model,
                or a value does not fit it.

        Example:
            >>> class Reading(DomainModel):
            ...     value: int = 0
            ...     note: str = ""
            >>> Reading(value=3).with_changes(note="rising")
            Reading(value=3, note='rising')
            >>> Reading(value=3).with_changes(vlaue=4)
            Traceback (most recent call last):
                ...
            pydantic_core._pydantic_core.ValidationError: ...
        """
        return self.model_validate({**dict(self), **changes})
