"""Pure decoders for the binary structures storage devices return.

No I/O and no platform knowledge: every function here takes ``bytes`` and
returns domain values, which is what allows the same tests to run on every
operating system.
"""

from __future__ import annotations

from .ata_identify import AtaIdentity, decode_identify, decode_vpd_ata_information
from .ata_smart import build_health, decode_attributes, decode_health
from .nvme import NvmeIdentity, decode_identify_controller, decode_smart_log
from .pciids import describe as describe_pci_device

__all__ = [
    "AtaIdentity",
    "NvmeIdentity",
    "build_health",
    "decode_attributes",
    "decode_health",
    "decode_identify",
    "decode_identify_controller",
    "decode_smart_log",
    "decode_vpd_ata_information",
    "describe_pci_device",
]
