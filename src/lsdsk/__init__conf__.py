"""Static package metadata surfaced to CLI commands and documentation.

Purpose
-------
Expose the current project metadata as simple constants. These values are kept
in sync with ``pyproject.toml`` by development automation (tests, push
pipelines), so runtime code does not query packaging metadata.

Contents
--------
* Module-level constants describing the published package.
* :func:`print_info` rendering the constants for the CLI ``info`` command.

System Role
-----------
Lives in the adapters/platform layer; CLI transports import these constants to
present authoritative project information without invoking packaging APIs. It
writes through ``adapters.cli.safe_console`` for the same reason every other view
does - an unencodable glyph must degrade rather than abort, and a reader that
leaves must leave 141 - which is an adapter importing an adapter, not a layer
crossed.

That import is made INSIDE :func:`print_info`, because at module scope it is a
cycle: ``adapters.cli.__init__`` imports ``root``, and ``root`` reads
``__init__conf__.title`` while building its command group, so the constants below
are not bound yet and the import fails with a partially initialized module.
"""

from __future__ import annotations

__all__ = [
    "LAYEREDCONF_APP",
    "LAYEREDCONF_SLUG",
    "LAYEREDCONF_VENDOR",
    "author",
    "author_email",
    "homepage",
    "name",
    "print_info",
    "shell_command",
    "title",
    "version",
]

#: Distribution name declared in ``pyproject.toml``.
name = "lsdsk"
#: Human-readable summary shown in CLI help output.
title = "See your disks and controllers, and what is wrong with how they are connected"
#: Current release version pulled from ``pyproject.toml`` by automation.
version = "1.2.15"
#: Repository homepage presented to users.
homepage = "https://github.com/bitranox/lsdsk"
#: Author attribution surfaced in CLI output.
author = "bitranox"
#: Contact email surfaced in CLI output.
author_email = "bitranox@gmail.com"
#: Console-script name published by the package.
shell_command = "lsdsk"

#: Vendor identifier for lib_layered_config paths (macOS/Windows)
LAYEREDCONF_VENDOR: str = "bitranox"
#: Application display name for lib_layered_config paths (macOS/Windows)
LAYEREDCONF_APP: str = "lsdsk"
#: Configuration slug for lib_layered_config Linux paths and environment variables
LAYEREDCONF_SLUG: str = "lsdsk"


def print_info() -> None:
    """Print the summarised metadata block used by the CLI ``info`` command.

    Why
        Provides a single, auditable rendering function so documentation and
        CLI output always match the system design reference.

    Side Effects
        Writes to ``stdout``.

    Examples:
    --------
    >>> print_info()  # doctest: +ELLIPSIS
    Info for lsdsk:
    ...
    """
    fields = [
        ("name", name),
        ("title", title),
        ("version", version),
        ("homepage", homepage),
        ("author", author),
        ("author_email", author_email),
        ("shell_command", shell_command),
    ]
    pad = max(len(label) for label, _ in fields)
    lines = [f"Info for {name}:", ""]
    lines.extend(f"    {label.ljust(pad)} = {value}" for label, value in fields)
    # Through the guarded sink, not sys.stdout: this text is a few hundred bytes,
    # far under Python's 8 KB block buffer, so a raw write reached the OS only at
    # the interpreter's own exit flush. A reader that had gone by then broke the
    # pipe OUTSIDE every handler, and the run left 120 - CPython's shutdown-flush
    # code, which is not an ExitCode member and appears in no document this tool
    # ships. echo writes and flushes at a point the guard can still see.
    from .adapters.cli import safe_console  # noqa: PLC0415 - deferred: breaks a cycle (see below)

    safe_console.echo("\n".join(lines))
