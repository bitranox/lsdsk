"""The desktop launcher's uvx command, and its no-drift tie to pyproject.

A shortcut starts lsdsk with ``uvx --exclude-newer "7 days" <owned exemptions>
lsdsk@latest``. The cooldown holds back any release younger than it across the
whole dependency tree; the exemptions keep lsdsk and the libraries lsdsk owns
uncooled, because a ``uvx`` run from the user's Desktop does not read lsdsk's own
``[tool.uv]``. These pin the command's shape and tie the exemption set to
pyproject, so the two cannot drift apart as the owned closure changes.
"""

from __future__ import annotations

import itertools
import tomllib
from pathlib import Path

import pytest

from lsdsk.domain.shortcut import OWNED_CLOSURE, UVX_COOLDOWN, uvx_launch_args

_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


@pytest.mark.os_agnostic
def test_the_launch_command_pins_the_cooldown_and_ends_at_lsdsk_latest() -> None:
    """The resolved uvx path leads, the cooldown follows, lsdsk@latest closes it."""
    args = uvx_launch_args("/usr/bin/uvx")

    assert args[0] == "/usr/bin/uvx"
    assert args[1:3] == ("--exclude-newer", UVX_COOLDOWN)
    assert args[-1] == "lsdsk@latest"


@pytest.mark.os_agnostic
def test_every_owned_package_is_exempted_as_its_own_pair() -> None:
    """Each owned package rides on the line as ``--exclude-newer-package PKG=false``."""
    args = uvx_launch_args("uvx")
    pairs = list(itertools.pairwise(args))

    for package in OWNED_CLOSURE:
        assert ("--exclude-newer-package", f"{package}=false") in pairs


@pytest.mark.os_agnostic
def test_the_exemption_set_cannot_drift_from_pyproject() -> None:
    """The baked closure equals lsdsk plus pyproject's ``= false`` entries, cooldown included.

    The command line carries the exemptions because the published ``[tool.uv]`` is
    not read from the Desktop; this holds the baked copy equal to the source of
    truth so a new owned library added to pyproject is added here too.
    """
    tool_uv = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"]
    false_keys = {name for name, value in tool_uv["exclude-newer-package"].items() if value is False}

    assert set(OWNED_CLOSURE) == {"lsdsk"} | false_keys
    assert tool_uv["exclude-newer"] == UVX_COOLDOWN
