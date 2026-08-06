"""Application layer - port definitions.

Declares the contracts adapters satisfy, as callable Protocols. Depends on the
domain and on nothing else.

Contents:
    * :mod:`.ports` - Protocol definitions for every adapter function
"""

from __future__ import annotations

from .ports import (
    DeployConfiguration,
    DisplayConfig,
    GetConfig,
    GetDefaultConfigPath,
    InitLogging,
)

__all__ = [
    "DeployConfiguration",
    "DisplayConfig",
    "GetConfig",
    "GetDefaultConfigPath",
    "InitLogging",
]
