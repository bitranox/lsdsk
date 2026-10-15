r"""The premise every test of an undecodable file name stands on, checked once for both files that need it.

Those tests plant a name holding a byte that is not valid UTF-8 and expect the
operating system to hand it back as a lone surrogate (``\udcff``), which is what
Python does when the filesystem encoding is UTF-8 with ``surrogateescape``. Under a
single-byte locale (``LC_ALL=de_AT.iso88591 PYTHONUTF8=0``) every byte decodes to an
ordinary character, so no surrogate ever comes back and the test fails on its own
premise rather than on the code - measured as 2 failed in a whole-suite run.

Asked by decoding the byte rather than by reading the encoding's name, because the
name does not say which error handler applies: Windows decodes with
``surrogatepass``, which refuses ``\xff`` outright, and that is caught here so the
condition can be evaluated at import on every platform.
"""

from __future__ import annotations

import os

import pytest


def the_filesystem_hands_back_a_lone_surrogate() -> bool:
    r"""Whether an undecodable byte in a file name comes back as ``\udcff`` here.

    Returns:
        True where the filesystem encoding is UTF-8 with ``surrogateescape``.

    Example:
        >>> isinstance(the_filesystem_hands_back_a_lone_surrogate(), bool)
        True
    """
    try:
        return os.fsdecode(b"\xff") == "\udcff"
    except UnicodeDecodeError:
        return False


#: Skip a test whose premise is an operating-system name no UTF-8 text can hold.
NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM = pytest.mark.skipif(
    not the_filesystem_hands_back_a_lone_surrogate(),
    reason="the filesystem encoding decodes every byte, so no name comes back as a lone surrogate",
)

__all__ = ["NEEDS_A_SURROGATE_ESCAPING_FILESYSTEM", "the_filesystem_hands_back_a_lone_surrogate"]
