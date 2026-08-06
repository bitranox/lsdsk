"""Port contract tests.

Each port is exercised through its in-memory implementation, so the contract is
proved against a real object rather than a mock.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from lib_layered_config import Config

from lsdsk.adapters.memory import (
    get_config_in_memory,
    get_default_config_path_in_memory,
    init_logging_in_memory,
)
from lsdsk.composition import AppServices, build_production, build_testing

if TYPE_CHECKING:
    from collections.abc import Callable

    from lsdsk.application.ports import GetConfig, GetDefaultConfigPath, InitLogging


@pytest.fixture
def get_config_impl() -> GetConfig:
    """Provide the in-memory GetConfig implementation."""
    return get_config_in_memory


@pytest.fixture
def get_default_config_path_impl() -> GetDefaultConfigPath:
    """Provide the in-memory GetDefaultConfigPath implementation."""
    return get_default_config_path_in_memory


@pytest.fixture
def init_logging_impl() -> InitLogging:
    """Provide the in-memory InitLogging implementation."""
    return init_logging_in_memory


@pytest.mark.os_agnostic
def test_get_config_returns_config_with_dict(get_config_impl: GetConfig) -> None:
    """Verify GetConfig returns a Config whose as_dict yields a dict."""
    config = get_config_impl()

    assert isinstance(config, Config)
    assert isinstance(config.as_dict(), dict)


@pytest.mark.os_agnostic
def test_get_default_config_path_returns_toml_path(get_default_config_path_impl: GetDefaultConfigPath) -> None:
    """Verify GetDefaultConfigPath returns a path ending in .toml."""
    path = get_default_config_path_impl()

    assert isinstance(path, Path)
    assert path.suffix == ".toml"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("build", [build_production, build_testing])
def test_every_service_container_is_fully_populated_and_callable(build: Callable[[], AppServices]) -> None:
    """Verify both wirings fill every port with something callable."""
    services = build()

    assert isinstance(services, AppServices)
    for field in AppServices.__dataclass_fields__:
        implementation = getattr(services, field)
        assert implementation is not None, f"{field} was left unwired"
        assert callable(implementation), f"{field} is not callable"
