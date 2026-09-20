"""A replay must name a device the way the CAPTURING machine did.

Resolving a PCI vendor and device identifier to a name is a lookup in a file,
not something the hardware says, so a builder that did it would name a capture
after whichever machine renders it - a Windows capture read on this box would be
named from a Linux distribution's hwdata package. That is why both readers
resolve the names and record them in the capture, and why the builders take what
the capture carries.

The instrument here is the database loader itself: it is replaced with one that
raises, so a build that reaches this machine's ``pci.ids`` fails rather than
quietly succeeding with the right answer for the wrong reason. Its control is
the same build over a capture carrying no names, which MUST reach it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.decode import pciids
from lsdsk.adapters.hw.snapshot import build_from

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

#: A name no pci.ids on earth carries, so finding it proves it came from the capture.
PLANTED = "Planted Storage Widget 9000"


def capture(name: str) -> dict[str, Any]:
    """Load one committed capture as the mapping a reader produced."""
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def refuse_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any read of this machine's database a loud failure."""

    def never(*_args: object, **_kwargs: object) -> pciids.Database:
        message = "the builder reached this machine's pci.ids"
        raise AssertionError(message)

    pciids.reset_database_cache()
    monkeypatch.setattr(pciids, "_load_database", never)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "name",
    ["linux-sas-hba.json", "linux-nvme-board.json", "linux-minimal.json"],
)
def test_a_capture_carrying_names_is_built_without_reading_this_machine(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every committed Linux capture carries its own names and must need no others."""
    payload = capture(name)
    assert payload.get("pci_names"), f"{name} carries no pci_names, so this proves nothing"
    refuse_this_machine(monkeypatch)
    machine = build_from(payload)
    assert machine.controllers, name


@pytest.mark.os_agnostic
def test_a_capture_carrying_no_names_is_what_still_reaches_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: without this the test above could pass on a builder that names nothing.

    It also states the residue plainly. A capture taken before ``pci_names``
    existed has nothing for the builder to name from, so that one case does fall
    back on the replaying machine - which is what the builders' docstrings say.
    """
    payload = capture("linux-sas-hba.json")
    payload.pop("pci_names")
    refuse_this_machine(monkeypatch)
    with pytest.raises(AssertionError, match=r"reached this machine's pci\.ids"):
        build_from(payload)


@pytest.mark.os_agnostic
def test_a_windows_capture_is_named_by_the_windows_machine_that_took_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The half that did not exist: Windows recorded no names, so Linux named its devices.

    The planted name cannot come from any database, so its appearance is proof
    the capture's own record was used rather than a lookup here.
    """
    payload = capture("windows-ahci.json")
    # Every pair, because which entry is a storage controller is the builder's
    # business and a test that picked one would be asserting its own guess.
    payload["pci_names"] = {
        f"{int(entry['vendor'], 16):04x}:{int(entry['device'], 16):04x}": PLANTED
        for entry in payload["pci"].values()
        if entry.get("vendor") and entry.get("device")
    }
    refuse_this_machine(monkeypatch)

    machine = build_from(payload)
    assert any(controller.name == PLANTED for controller in machine.controllers), [
        controller.name for controller in machine.controllers
    ]


@pytest.mark.os_agnostic
def test_nothing_outside_the_lookup_module_resolves_a_name_for_itself() -> None:
    """One implementation of name resolution, held where a second one would appear.

    Both readers had the same few lines to write and only one of them had them;
    a copy in the other is how the two would come to spell a name differently
    for one device. The two lookups are the ingredients, so anything calling
    them outside their own module is that second copy starting.
    """
    lookups = {"lookup_device", "lookup_vendor"}
    offenders: list[str] = []
    for path in sorted((Path(__file__).parent.parent / "src" / "lsdsk").rglob("*.py")):
        if path.name == "pciids.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if called in lookups:
                offenders.append(f"{path.name}:{node.lineno} calls {called}")

    assert not offenders, "a second name resolver is growing: " + "; ".join(offenders)


@pytest.mark.os_agnostic
def test_the_one_resolver_is_what_both_readers_record_with() -> None:
    """And the control for the test above: the lookups ARE reachable from it.

    Without this, a rename of either lookup would empty the sweep above and it
    would pass by finding nothing rather than by finding nothing wrong.
    """
    known = pciids.Database({0x1000: "Broadcom"}, {(0x1000, 0x0097): "SAS3008"})
    entries = {"anything": {"vendor": "0x1000", "device": "0x0097"}}
    assert pciids.resolve_names(entries, known) == {"1000:0097": "Broadcom SAS3008"}

    source = ast.parse((Path(pciids.__file__)).read_text(encoding="utf-8"))
    resolver = next(
        node for node in ast.walk(source) if isinstance(node, ast.FunctionDef) and node.name == "resolve_names"
    )
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(resolver)
        if isinstance(node, ast.Call)
    }
    assert {"lookup_device", "lookup_vendor"} <= called, called
