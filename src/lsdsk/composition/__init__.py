"""Composition root wiring adapters to application ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib_layered_config import generate_examples

from .. import __init__conf__
from ..adapters.config.deploy import deploy_configuration
from ..adapters.config.display import display_config

# Configuration services
from ..adapters.config.loader import get_config

# Logging services
from ..adapters.logging.setup import init_logging

# Static conformance assertions - pyright verifies that each adapter function
# structurally satisfies its corresponding Protocol at type-check time.
if TYPE_CHECKING:
    from ..application.ports import (
        DeployConfiguration,
        DisplayConfig,
        GenerateExamples,
        GetConfig,
        InitLogging,
        PrintInfo,
    )

    _assert_print_info: PrintInfo = __init__conf__.print_info
    _assert_get_config: GetConfig = get_config
    _assert_generate_examples: GenerateExamples = generate_examples
    _assert_deploy_configuration: DeployConfiguration = deploy_configuration
    _assert_display_config: DisplayConfig = display_config
    _assert_init_logging: InitLogging = init_logging


@dataclass(frozen=True, slots=True)
class AppServices:
    """Frozen container holding all application port implementations."""

    get_config: GetConfig
    generate_examples: GenerateExamples
    deploy_configuration: DeployConfiguration
    display_config: DisplayConfig
    init_logging: InitLogging
    print_info: PrintInfo


def build_production() -> AppServices:
    """Wire production adapters into an AppServices container.

    Returns:
        The services every command resolves its ports through.
    """
    return AppServices(
        get_config=get_config,
        generate_examples=generate_examples,
        deploy_configuration=deploy_configuration,
        display_config=display_config,
        init_logging=init_logging,
        print_info=__init__conf__.print_info,
    )


def build_testing() -> AppServices:
    """Wire in-memory adapters into an AppServices container.

    Returns:
        AppServices container with in-memory adapters.
    """
    from ..adapters.memory import (  # noqa: PLC0415 - deferred: keeps in-memory test doubles out of the production import path
        deploy_configuration_in_memory,
        display_config_in_memory,
        generate_examples_in_memory,
        get_config_in_memory,
        init_logging_in_memory,
        print_info_in_memory,
    )

    return AppServices(
        get_config=get_config_in_memory,
        generate_examples=generate_examples_in_memory,
        deploy_configuration=deploy_configuration_in_memory,
        display_config=display_config_in_memory,
        init_logging=init_logging_in_memory,
        print_info=print_info_in_memory,
    )


__all__ = [
    "AppServices",
    "build_production",
    "build_testing",
    "deploy_configuration",
    "display_config",
    "generate_examples",
    "get_config",
    "init_logging",
]
