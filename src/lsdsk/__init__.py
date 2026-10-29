"""Public package surface: configuration and package metadata.

lsdsk is a command-line tool, so what this module exports is deliberately small:
configuration and package metadata, which is what a program embedding the tool
asks the package itself for. Everything a reader wants is behind the CLI or, for
a program, behind ``lsdsk <command> --format json``.

That is not the whole Python API. A caller who wants the hardware without a
subprocess reaches into the modules directly - ``lsdsk.adapters.hw.snapshot``
for the reading and ``lsdsk.domain.diagnostics`` for the rules - which
``skills/lsdsk/SKILL.md`` documents. Read this ``__all__`` as the front door
rather than as the extent of what is callable.
"""

#: ``typing.TYPE_CHECKING`` without importing ``typing``: see :func:`__getattr__`.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from .__init__conf__ import print_info
    from .composition import get_config


def __getattr__(name: str) -> object:
    """Import an exported attribute on first use rather than with the package.

    The console script's first act is ``from lsdsk.entry import main``, which
    imports this package before any handler of lsdsk exists, so everything this
    module imports is a stretch in which a Ctrl+C prints a traceback. Importing
    the composition here made that a quarter of a second; importing the package
    metadata or even ``typing`` still left a window. So the package imports
    nothing, and the entry point imports what it needs inside the guard that
    answers an interrupt with 130.

    Args:
        name: The attribute asked for.

    Returns:
        ``get_config``, the configuration loader the composition wires, or
        ``print_info``, the metadata block the ``info`` command prints.

    Raises:
        AttributeError: For any other name, as a module without it would.
    """
    if name not in {"get_config", "print_info"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib  # noqa: PLC0415 - deferred: see the docstring

    # Each module named literally, so the guard against shelling out can read what is imported.
    if name == "get_config":
        return importlib.import_module(".composition", __name__).get_config
    return importlib.import_module(".__init__conf__", __name__).print_info


__all__ = [
    "get_config",
    "print_info",
]
