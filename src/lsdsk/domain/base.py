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

from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping

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

        It goes through validation, which pydantic's own
        ``model_copy(update=...)`` does not: that writes an unknown key straight
        into the instance, so ``update={"prots_used": 3}`` leaves ``ports_used``
        alone, sets an attribute nothing reads, dumps nothing extra and raises
        nothing (measured on pydantic 2.13.5). Here the typo is refused, and
        :meth:`model_copy` routes its ``update`` through this method for the
        same reason.

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

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Return a copy that answers from its own fields, with ``update`` validated.

        Pydantic copies an instance's ``__dict__`` whole, and a
        ``functools.cached_property`` keeps its value there, so a copy made after
        an index was first used carried that index along. With ``update`` the
        copy's fields and its index then described two different values: a
        copied ``History`` answered ``for_identity`` from the series it no longer
        held. Every cached value is dropped from the copy, which rebuilds its own
        on first use, and ``update`` goes through :meth:`with_changes`, so a
        misspelt field is refused rather than written into the instance.

        Args:
            update: Field values to replace, by field name.
            deep: Whether to copy the field values as well as the instance.

        Returns:
            A new instance of the same type.

        Raises:
            pydantic.ValidationError: If a name in ``update`` is not a field of
                this model, or a value does not fit it.

        Example:
            >>> class Reading(DomainModel):
            ...     value: int = 0
            >>> Reading(value=3).model_copy(update={"value": 4})
            Reading(value=4)
        """
        copied = super().model_copy(deep=deep)
        for name in _cached_names(type(self)):
            vars(copied).pop(name, None)
        return copied.with_changes(**update) if update else copied


def _cached_names(model: type[DomainModel]) -> tuple[str, ...]:
    """Every ``cached_property`` a model class carries, inherited ones included.

    Args:
        model: The model class.

    Returns:
        The attribute names the cached values are stored under.
    """
    return tuple(
        name for klass in model.__mro__ for name, member in vars(klass).items() if isinstance(member, cached_property)
    )
