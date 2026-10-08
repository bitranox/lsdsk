"""Each deploy target reads its OWN configured directory and file mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.config.permissions import get_modes_for_target
from lsdsk.domain.enums import DeployTarget

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib_layered_config import Config

#: Six distinct figures, so a target answering with another target's value cannot pass.
_CONFIGURED: dict[str, int] = {
    "app_directory": 0o751,
    "app_file": 0o641,
    "host_directory": 0o752,
    "host_file": 0o642,
    "user_directory": 0o753,
    "user_file": 0o643,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("target", "directory", "file"),
    [
        (DeployTarget.APP, 0o751, 0o641),
        (DeployTarget.HOST, 0o752, 0o642),
        (DeployTarget.USER, 0o753, 0o643),
    ],
)
def test_a_target_answers_with_its_own_configured_modes(
    config_factory: Callable[[dict[str, Any]], Config],
    target: DeployTarget,
    directory: int,
    file: int,
) -> None:
    """The HOST arms of ``dir_mode_for`` and ``file_mode_for`` were reached by no test."""
    config = config_factory({"lib_layered_config": {"default_permissions": dict(_CONFIGURED)}})

    modes = get_modes_for_target(target, config)

    assert (modes.directory, modes.file) == (directory, file)
