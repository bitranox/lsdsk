"""Module entry point for ``python -m lsdsk``, which is also how the elevated copy starts."""

from .entry import main

if __name__ == "__main__":
    raise SystemExit(main())
