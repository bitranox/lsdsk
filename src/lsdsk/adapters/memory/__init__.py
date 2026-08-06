"""In-memory adapter implementations for testing.

Provides lightweight implementations of all application ports that operate
entirely in memory: no filesystem, no logging framework.

Contents:
    * :mod:`.config` - In-memory configuration adapters
    * :mod:`.history` - In-memory counter history
    * :mod:`.logging` - In-memory logging adapter
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import (
    deploy_configuration_in_memory,
    display_config_in_memory,
    get_config_in_memory,
    get_default_config_path_in_memory,
)
from .history import read_history_in_memory, reset_history_in_memory, write_history_in_memory
from .info import print_info_in_memory, reset_info_in_memory
from .logging import init_logging_in_memory

# Static conformance assertions
if TYPE_CHECKING:
    from lsdsk.application.ports import (
        DeployConfiguration,
        DisplayConfig,
        GetConfig,
        GetDefaultConfigPath,
        InitLogging,
        ReadHistory,
        WriteHistory,
    )

    _assert_get_config: GetConfig = get_config_in_memory
    _assert_get_default_config_path: GetDefaultConfigPath = get_default_config_path_in_memory
    _assert_deploy_configuration: DeployConfiguration = deploy_configuration_in_memory
    _assert_display_config: DisplayConfig = display_config_in_memory
    _assert_init_logging: InitLogging = init_logging_in_memory
    _assert_read_history: ReadHistory = read_history_in_memory
    _assert_write_history: WriteHistory = write_history_in_memory

__all__ = [
    "deploy_configuration_in_memory",
    "display_config_in_memory",
    "get_config_in_memory",
    "get_default_config_path_in_memory",
    "init_logging_in_memory",
    "print_info_in_memory",
    "read_history_in_memory",
    "reset_history_in_memory",
    "reset_info_in_memory",
    "write_history_in_memory",
]
