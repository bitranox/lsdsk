"""Strictly-typed wrappers for rich_click's partially-typed decorators.

rich_click ships ``py.typed``, but its ``option`` decorator is typed with a
partially-unknown return, so the strict type checker reports
``reportUnknownMemberType`` at every call site. click's own decorators
are fully typed, but they default the parameter class to ``click.Option`` rather
than rich_click's ``RichOption``, which would change help rendering.

This module forwards to rich_click's decorators (so ``RichOption``/``RichArgument``
are still used at runtime) through a typed ``Protocol``: the ``cast`` is a no-op
at runtime, and the wrappers keep the exact same public signatures they always
had, so no rule needs to be silenced anywhere.

Other click members (``command``, ``group``, ``echo``, ``Context``, ``Path`` ...)
type cleanly and are still used directly as ``click.X`` at call sites.

``version_option`` was wrapped here too until the root group took over printing the
version itself: click's own printer writes through click's echo, and a reader that
has gone then makes ``cli.main()`` catch the ``EPIPE`` and exit 1 before any handler
here is reached. Nothing wraps that decorator now, so nothing here declares it.
"""

import os
from collections.abc import Callable
from typing import Any, Protocol, cast

import rich_click as click

# Click decorators turn a command function (or another decorator's result) into a
# wrapped callable. ``Any`` is fully known to the type checker, so this alias stays
# clean and works both as ``@option(...)`` and as a value collected into a list.
_CommandDecorator = Callable[[Callable[..., Any]], Callable[..., Any]]


class _RichClickDecorators(Protocol):
    """rich_click's decorator surface, declared with complete types."""

    option: Callable[..., _CommandDecorator]


# ``cast`` is type-only; at runtime these forward to rich_click's own decorators,
# so ``RichOption``/``RichArgument`` behavior is unchanged.
_click = cast("_RichClickDecorators", click)


class NonEmptyPath(click.Path):
    """A path option that refuses the empty string, which names no path.

    ``Path("")`` is the current directory, so an empty value used to be acted on
    as ``.``: a capture written to ``.`` failed late with an errno, and an
    example directory of ``""`` wrote into whatever directory the command ran
    from. This is the one place that says so, for every option that names a path
    the command acts on. A subclass that gives the empty string a meaning of its
    own (``--history-file``) overrides :meth:`convert` before this check.
    """

    def convert(self, value: str | os.PathLike[str], param: click.Parameter | None, ctx: click.Context | None) -> Any:
        """Refuse an empty value and convert any other as :class:`click.Path` does.

        Args:
            value: What the command line carried.
            param: The option being converted.
            ctx: The invocation.

        Returns:
            What :class:`click.Path` makes of `value`.
        """
        if value == "":
            self.fail("a path cannot be empty", param, ctx)
        return super().convert(value, param, ctx)


def option(*param_decls: str, **attrs: Any) -> _CommandDecorator:
    """Typed wrapper over :func:`rich_click.option`. See module docstring.

    Args:
        param_decls: The option's spellings, as rich-click takes them.
        attrs: Every other rich-click keyword, forwarded unchanged.

    Returns:
        The decorator rich-click returns, with a type its callers can read.
    """
    return _click.option(*param_decls, **attrs)


__all__ = ["NonEmptyPath", "option"]
