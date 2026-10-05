"""The order drives are listed in: by the number a reader counts them by.

A plain string compare puts ``PhysicalDrive10`` before ``PhysicalDrive2`` and
``sdaa`` before ``sdz``, so on a machine with many drives the listing stopped
following the drive numbers the operating system shows everywhere else.

System Role:
    Domain layer. Pure text to a sort key.
"""

from __future__ import annotations

import re

#: A run of digits, or a run of anything else; a name is read as alternating runs.
_RUNS = re.compile(r"(\d+)")

#: A Linux disk named by letters rather than digits, counted on past ``z`` the
#: way the kernel does it (``sdz``, ``sdaa``): SCSI and SATA, the old IDE,
#: virtio and Xen. The prefix keeps a path in front of the name.
_LETTERED = re.compile(r"^(.*?\b(?:sd|hd|vd|xvd))([a-z]+)(\d*)$")

_LETTERS_IN_ALPHABET = 26

#: One piece of a name: the text before a number, and that number (or -1 for a
#: piece with no number, so ``sda`` sorts before ``sda1``).
Piece = tuple[str, int]


def _letter_number(letters: str) -> int:
    """The position of a kernel disk letter sequence: a=1, z=26, aa=27.

    Example:
        >>> [_letter_number(letters) for letters in ("a", "z", "aa", "ab", "zz", "aaa")]
        [1, 26, 27, 28, 702, 703]
    """
    number = 0
    for letter in letters:
        number = number * _LETTERS_IN_ALPHABET + (ord(letter) - ord("a") + 1)
    return number


def _pieces(name: str) -> tuple[Piece, ...]:
    """Split a name into (text, number) pieces, a run of text then its number."""
    runs = _RUNS.split(name)
    pieces: list[Piece] = []
    for index in range(0, len(runs), 2):
        digits = runs[index + 1] if index + 1 < len(runs) else ""
        pieces.append((runs[index], int(digits) if digits else -1))
    return tuple(pieces)


def disk_name_order(name: str) -> tuple[tuple[Piece, ...], str]:
    r"""Return the key that lists drives by the numbers in their names.

    Every number in the name is compared as a number, and a lettered Linux name
    counts its letters the way the kernel assigned them. Which family comes
    first (``nvme`` before ``sd``) is unchanged from a plain compare; only the
    order inside a family is. The name itself is the last field, so two
    spellings of one number (``nvme0n1``, ``nvme00n1``) still get distinct keys.

    Args:
        name: A drive's node name, such as ``sda``, ``nvme0n1`` or
            ``PhysicalDrive3``, or a path ending in one.

    Returns:
        A key for ``sorted``.

    Example:
        >>> sorted(["PhysicalDrive10", "PhysicalDrive2"], key=disk_name_order)
        ['PhysicalDrive2', 'PhysicalDrive10']
        >>> sorted(["sdaa", "sdz", "nvme10n1", "nvme2n1"], key=disk_name_order)
        ['nvme2n1', 'nvme10n1', 'sdz', 'sdaa']
    """
    lettered = _LETTERED.match(name)
    if lettered is None:
        return _pieces(name), name
    prefix, letters, partition = lettered.groups()
    disk: Piece = (prefix, _letter_number(letters))
    return ((disk, ("", int(partition))) if partition else (disk,)), name


__all__ = ["disk_name_order"]
