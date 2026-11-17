"""The configuration reader's platform layout, or ``None`` where it reads no user layer.

lib_layered_config 7.7.0 has no layout for a platform other than Linux, macOS
and Windows (FreeBSD, Solaris, Cygwin), and its resolver's ``strategy`` is
``None`` there. From 8.0.0 those platforms read the Linux layout, and
``strategy`` is never ``None``. lsdsk declares ``>=7.7.0``, so both can be
installed, and a direct ``resolver.strategy is None`` check is then either
needed (7.7.0) or, under strict pyright, an always-false comparison (8.0.0).
Returning the value through this function's declared type keeps one check
correct against both. Remove the module once the floor reaches 8.0.0.

System Role:
    Adapter-layer compatibility seam over the configuration library's resolver.

Contents:
    * :func:`reader_strategy` - the resolver's layout, typed as optional.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib_layered_config.adapters.path_resolvers._base import PlatformStrategy
    from lib_layered_config.adapters.path_resolvers.default import DefaultPathResolver

__all__ = ["reader_strategy"]


def reader_strategy(resolver: DefaultPathResolver) -> PlatformStrategy | None:
    """The layout ``resolver`` reads with, or ``None`` where the library reads no user layer.

    Args:
        resolver: The configuration library's path resolver for one platform.

    Returns:
        The platform strategy, or ``None`` on a platform lib_layered_config
        7.7.0 has no layout for. 8.0.0 always returns one.

    Example:
        >>> from lib_layered_config.adapters.path_resolvers.default import DefaultPathResolver
        >>> resolver = DefaultPathResolver(vendor="Acme", app="Demo", slug="demo", platform="linux")
        >>> reader_strategy(resolver) is not None
        True
    """
    return resolver.strategy
