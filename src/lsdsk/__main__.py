"""Module entry point for ``python -m lsdsk``, which is also how the elevated copy starts."""

from __future__ import annotations

from .entry import main

if __name__ == "__main__":
    raise SystemExit(main())
