"""In-memory adapter implementations for testing.

Provides lightweight implementations of all application ports that operate
entirely in memory: no filesystem, no logging framework.

There is no in-memory counter history here, and its absence is deliberate. Two
doubles stood in this package for ports nothing resolved through the container,
so a test wiring :func:`~lsdsk.composition.build_testing` believed it had an
in-memory store and wrote to the real one. The counter store is injected by its
PATH instead, which every history test already does.

Contents:
    * :mod:`.config` - In-memory configuration adapters
    * :mod:`.info` - In-memory metadata printer
    * :mod:`.logging` - In-memory logging adapter
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import (
    deploy_configuration_in_memory,
    display_config_in_memory,
    get_config_in_memory,
)
from .info import print_info_in_memory, reset_info_in_memory
from .logging import init_logging_in_memory

# Static conformance assertions
if TYPE_CHECKING:
    from lsdsk.application.ports import (
        DeployConfiguration,
        DisplayConfig,
        GetConfig,
        InitLogging,
    )

    _assert_get_config: GetConfig = get_config_in_memory
    _assert_deploy_configuration: DeployConfiguration = deploy_configuration_in_memory
    _assert_display_config: DisplayConfig = display_config_in_memory
    _assert_init_logging: InitLogging = init_logging_in_memory

__all__ = [
    "deploy_configuration_in_memory",
    "display_config_in_memory",
    "get_config_in_memory",
    "init_logging_in_memory",
    "print_info_in_memory",
    "reset_info_in_memory",
]
