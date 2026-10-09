"""A DMI string names a hypervisor only when it says so, never by containing its letters.

``classify`` reads the vendor and product strings a firmware publishes. A bare
substring test turned a Surface laptop into a Hyper-V guest (its vendor is
"Microsoft Corporation"), a Chromebook into a Google Compute Engine instance
("Google") and a ThinkPad Xenon into a Xen guest, and a machine called a guest
skips every link, port and fabric rule.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode.virtualization import VirtualizationEvidence, classify
from lsdsk.domain.enums import Environment


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("vendor", "product"),
    [
        ("Microsoft Corporation", "Surface Laptop 4"),
        ("Google", "Kaisen"),
        ("LENOVO", "ThinkPad Xenon"),
        ("Dell Inc.", "PowerEdge R750"),
        ("Gigabyte Technology Co., Ltd.", "Z690 AORUS ELITE"),
    ],
)
def test_bare_metal_dmi_stays_bare_metal(vendor: str, product: str) -> None:
    verdict = classify(VirtualizationEvidence(dmi_vendor=vendor, dmi_product=product))
    assert verdict.environment is Environment.BARE_METAL, verdict


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("vendor", "product", "detail"),
    [
        ("QEMU", "Standard PC (Q35 + ICH9, 2009)", "QEMU"),
        ("VMware, Inc.", "VMware Virtual Platform", "VMware"),
        ("VMware, Inc.", "VMware7,1", "VMware"),
        ("innotek GmbH", "VirtualBox", "VirtualBox"),
        ("Microsoft Corporation", "Virtual Machine", "Hyper-V"),
        ("Google", "Google Compute Engine", "Google Compute Engine"),
        ("Amazon EC2", "t3.micro", "Amazon EC2"),
        ("Xen", "HVM domU", "Xen"),
        ("Red Hat", "KVM", "KVM"),
        ("Bochs", "Bochs", "QEMU"),
    ],
)
def test_hypervisor_dmi_stays_detected(vendor: str, product: str, detail: str) -> None:
    verdict = classify(VirtualizationEvidence(dmi_vendor=vendor, dmi_product=product))
    assert (verdict.environment, verdict.detail) == (Environment.VIRTUAL_MACHINE, detail)
