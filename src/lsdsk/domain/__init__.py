"""Domain layer - pure business logic with no I/O or framework dependencies.

Contains the vocabulary of storage topology and health, and the rules that turn
a reading of a machine into findings about it.

Contents:
    * :mod:`.models` - Controller, Disk, Health, Finding and the link models
    * :mod:`.diagnostics` - the pure rules that produce findings
    * :mod:`.enums` - domain enumerations
    * :mod:`.errors` - domain exception types
"""

from __future__ import annotations

from .diagnostics import count_by_severity, diagnose
from .enums import BusType, ControllerKind, DeployTarget, DiskKind, OutputFormat, Severity
from .errors import ConfigurationError, UnsupportedPlatformError
from .models import Controller, Disk, Finding, Health, InterfaceLink, Inventory, PcieLink, PcieSlot

__all__ = [
    "BusType",
    "ConfigurationError",
    "Controller",
    "ControllerKind",
    "DeployTarget",
    "Disk",
    "DiskKind",
    "Finding",
    "Health",
    "InterfaceLink",
    "Inventory",
    "OutputFormat",
    "PcieLink",
    "PcieSlot",
    "Severity",
    "UnsupportedPlatformError",
    "count_by_severity",
    "diagnose",
]
