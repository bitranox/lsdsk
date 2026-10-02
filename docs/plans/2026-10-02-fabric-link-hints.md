# Fabric Link Hints Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use bitranox:process-agents-subagent-driven-development (recommended) or bitranox:process-plan-executor to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grade every PCIe link no storage rule grades, and raise a HINT where it runs on fewer lanes than both ends support (A) or where its slot caps the card (C), naming a free slot that would carry more.

**Architecture:** As decided in `docs/plans/2026-10-02-fabric-link-hints-design.md`. A new pure module `domain/fabric_links.py` enumerates links from `inventory.pci_tree` and grades them; `diagnose()` calls it under the physical-readings gate. The storage rule's free-slot search moves to `domain/placement.py`, keyed on a `Seat` value both a controller and a fabric link produce, and the two message formatters move to `domain/pcie_text.py` so the new module needs nothing from `diagnostics.py` (which imports it).

**Tech Stack:** Python 3.11+, pydantic 2 (`DomainModel`), pytest.

## Global Constraints

- Severity of every new finding: `Severity.HINT`. A hint leaves the exit code at 0.
- Not graded: a speed-only shortfall (B); a storage controller or any device a drive hangs off; a device whose parent does not face downstream; a link with either end's capability unread; a link that never trained (width 0) or whose width was not read.
- A device's link is paired with its PARENT only where the parent faces downstream: `port_kind` is `PciPortKind.ROOT` or `PciPortKind.SWITCH_DOWNSTREAM`.
- One finding per link, subject = the lowest function address of the device behind the port.
- A beats C on one link: a link that lost lanes gets only the lanes finding.
- Every PCIe figure in a sentence goes through `format_pcie_sentence` (`PCIe Gen3x8`), every GB/s through `format_gbytes` (`7.88 GB/s`).
- The storage findings over every committed capture must be byte-identical before and after Tasks 1-2.
- No subprocess, no network. No project-owned signature of 6+ parameters (`tests/test_interface_census.py`). Every name another module imports is in its module's `__all__` (`tests/test_data_architecture.py`). Every exported function has Google Args/Returns (`tests/test_exported_docstrings.py`). Same-typed multi-value returns get a NamedTuple.
- No em-dashes or other typographic tells in any file or commit message. No Claude/AI attribution in commits. Commit messages go through a file (`git commit -F`), written in an EARLIER command than the commit.
- Per task: `.venv/bin/python -m pytest -q -p no:cacheprovider <the task's tests>`, `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`, `.venv/bin/pyright --pythonpath .venv/bin/python` and with `-p pyrightconfig.windows.json`, `.venv/bin/lint-imports`. Before the release task: the full CI selection `.venv/bin/python -m pytest -q -p no:cacheprovider -m "not local_only"` in the foreground.
- `skills/lsdsk/SKILL.md` changes only through `bitranox:meta-skill-writer`.
- Run every command from the worktree root `.claude/worktrees/usb-link`.

## File Structure

| File                                     | Responsibility                                                             |
|------------------------------------------|----------------------------------------------------------------------------|
| `src/lsdsk/domain/pcie_text.py` (new)    | `format_gbytes`, `format_pcie_sentence` (moved from `diagnostics.py`)      |
| `src/lsdsk/domain/placement.py` (new)    | `Seat`, `seat_of`, `achievable_pcie`, `best_slot`, `free_slot_for`         |
| `src/lsdsk/domain/fabric_links.py` (new) | `FabricLink`, `fabric_links`, `carrying_clause`, `diagnose_fabric_links`   |
| `src/lsdsk/domain/models.py`             | `PciNode.faces_downstream`                                                 |
| `src/lsdsk/domain/diagnostics.py`        | imports the moved helpers back; `diagnose()` calls `diagnose_fabric_links` |
| `tests/test_fabric_links.py` (new)       | enumeration, carrying clause, both hints, the per-capture table            |
| `FINDINGS.md`, `de/FINDINGS.md`          | the new finding in "What it finds"                                         |
| `CHANGELOG.md`                           | the entry under 1.6.0                                                      |
| `skills/lsdsk/SKILL.md`                  | the findings it documents (skill writer)                                   |

---

### Task 0: Record the storage findings before anything moves

**Files:** none committed.

- [ ] **Step 1: Snapshot every finding of every committed capture**

```bash
S=$(mktemp -d) && echo "$S" > /tmp/fabric-links-scratch-path && .venv/bin/python - "$S/findings_before.json" <<'EOF'
import json, sys
from pathlib import Path
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose
out = {}
for path in sorted(Path("tests/fixtures/hw").glob("*.json")):
    inventory = build_from(json.loads(path.read_text(encoding="utf-8")))
    out[path.stem] = [finding.model_dump(mode="json") for finding in diagnose(inventory)]
Path(sys.argv[1]).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
print(sum(len(v) for v in out.values()), "findings recorded")
EOF
```

Expected: a line `N findings recorded` with N > 0. Keep the file; Task 2 compares against it.

---

### Task 1: Move the two message formatters below `diagnostics.py`

**Files:**
- Create: `src/lsdsk/domain/pcie_text.py`
- Modify: `src/lsdsk/domain/diagnostics.py` (delete `_format_gbytes` and `format_pcie_sentence`, ~lines 78-112; import them back)

**Interfaces:**
- Produces: `pcie_text.format_gbytes(value: float | None) -> str`, `pcie_text.format_pcie_sentence(speed_gtps: float | None, width: int | None) -> str`. `diagnostics.format_pcie_sentence` stays importable (re-exported, still in its `__all__`), because `tests/test_one_spelling_for_a_generation.py` imports it from there.

**Out of scope:** `adapters/render/theme.py`'s own formatter - the render side's spelling, held to this one by `tests/test_one_spelling_for_a_generation.py`.

**STOP conditions:** `diagnostics.py` uses `_format_gbytes` under another name; the one-spelling test fails after the move.

- [ ] **Step 1: Create `src/lsdsk/domain/pcie_text.py`**

```python
"""How a finding writes a PCIe figure and a bandwidth in a sentence.

Kept below every rule module so a rule can be written without importing the
module that runs all of them: ``diagnostics.diagnose`` calls the fabric-link
rules, so those rules cannot import ``diagnostics`` for its formatters.

System Role:
    Domain layer. Pure text from numbers.
"""

from __future__ import annotations

from .models import pcie_generation


def format_gbytes(value: float | None) -> str:
    """Render a GB/s figure for a message, or a placeholder when unknown.

    Args:
        value: The bandwidth in GB/s, or ``None`` if unread.

    Returns:
        The figure with two decimals and its unit, or ``unknown``.

    Example:
        >>> format_gbytes(7.876)
        '7.88 GB/s'
        >>> format_gbytes(None)
        'unknown'
    """
    return "unknown" if value is None else f"{value:.2f} GB/s"


def format_pcie_sentence(speed_gtps: float | None, width: int | None) -> str:
    """Render a PCIe link as a generation and width, for a sentence.

    Written closed and in the marketing form, as the render layer writes it in a
    column. One spelling for the whole tool: a finding that said ``PCIe 3.0x8``
    while the table above it said ``Gen3x8`` would be describing the same link in
    two hands, and a reader comparing the two has to work out that they agree.

    The domain cannot reach the render layer's formatter, so this is the second
    place that spelling is written, and the two are held together by a test in
    ``tests/test_one_spelling_for_a_generation.py`` rather than by convention.

    Args:
        speed_gtps: The link's speed in GT/s per lane, or ``None`` if unread.
        width: The link's negotiated lane count, or ``None`` if unread.

    Returns:
        The figure as a sentence writes it, or ``PCIe unknown`` when either half
        was not read - never a half-figure, which would read as a measurement.

    Example:
        >>> format_pcie_sentence(8.0, 8)
        'PCIe Gen3x8'
        >>> format_pcie_sentence(None, None)
        'PCIe unknown'
    """
    generation = pcie_generation(speed_gtps)
    if generation is None or width is None:
        return "PCIe unknown"
    return f"PCIe Gen{generation}x{width}"


__all__ = ["format_gbytes", "format_pcie_sentence"]
```

- [ ] **Step 2: In `diagnostics.py`, delete both functions and import them back**

Delete `def _format_gbytes` and `def format_pcie_sentence` with their bodies. Add beside the other relative imports:

```python
from .pcie_text import format_gbytes, format_pcie_sentence
```

Replace every remaining `_format_gbytes(` in `diagnostics.py` with `format_gbytes(`:

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("src/lsdsk/domain/diagnostics.py"); t = p.read_text(encoding="utf-8")
assert "def _format_gbytes" not in t and "def format_pcie_sentence" not in t
n = t.count("_format_gbytes(")
p.write_text(t.replace("_format_gbytes(", "format_gbytes("), encoding="utf-8")
print(n, "call sites renamed")
EOF
```

If `pcie_generation` is now unused in `diagnostics.py`, ruff reports it (F401): remove it from that import list. Keep `"format_pcie_sentence"` in `diagnostics.__all__`.

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_one_spelling_for_a_generation.py tests/test_diagnostics.py tests/test_exported_docstrings.py tests/test_data_architecture.py --doctest-modules src/lsdsk/domain/pcie_text.py` then ruff, both pyright configs, lint-imports.
Expected: all pass, 0 errors.

- [ ] **Step 4: Commit** (`refactor(domain): move the finding text formatters below diagnostics`)

---

### Task 2: One free-slot search, keyed on a `Seat`

**Files:**
- Create: `src/lsdsk/domain/placement.py`
- Modify: `src/lsdsk/domain/diagnostics.py` (`_achievable_pcie`, `_gain_in`, `_best_slot`, `_free_slot_for`, `_swap_slot_for`, `_lower`, `_lower_int`, ~lines 159-262)
- Test: `tests/test_placement.py` (new)

**Interfaces:**
- Produces:
  - `class Seat(DomainModel, frozen=True)`: `link: PcieLink`, `port: PcieLink | None = None`, `port_address: OptionalDeviceText = None`
  - `seat_of(controller: Controller) -> Seat`
  - `achievable_pcie(seat: Seat) -> tuple[float | None, int | None]`
  - `best_slot(seat: Seat, inventory: Inventory, candidates: Callable[[PcieSlot], bool]) -> PcieSlot | None`
  - `free_slot_for(seat: Seat, inventory: Inventory) -> PcieSlot | None`

**Out of scope:** `Inventory.placement_candidates` and `_placement_groups` in `models.py` - the grouping reads only what it documents, and `best_slot` reads nothing more than `_best_slot` did.

**STOP conditions:** the storage findings differ from Task 0's snapshot in any byte.

- [ ] **Step 1: Write the failing test `tests/test_placement.py`**

```python
"""The free-slot search is one search, whatever kind of card asks it."""

from __future__ import annotations

import pytest

from lsdsk.domain.models import Controller, Inventory, PcieLink, PcieSlot
from lsdsk.domain.placement import Seat, achievable_pcie, free_slot_for, seat_of

CARD = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=16)
NARROW = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=8)
WIDE = PcieLink(max_speed_gtps=8.0, max_width=16)


def _inventory(*, connector: bool | None) -> Inventory:
    return Inventory(
        hostname="h",
        slots=(
            PcieSlot(address="0000:00:02.0", link=NARROW, occupied=True, connector_present=True),
            PcieSlot(address="0000:00:03.0", link=WIDE, occupied=False, connector_present=connector),
        ),
    )


@pytest.mark.os_agnostic
def test_a_seat_is_what_both_ends_can_give() -> None:
    assert achievable_pcie(Seat(link=CARD, port=NARROW, port_address="0000:00:02.0")) == (8.0, 8)


@pytest.mark.os_agnostic
def test_an_unread_port_gives_no_seat_figure() -> None:
    assert achievable_pcie(Seat(link=CARD, port=PcieLink(), port_address="0000:00:02.0")) == (None, None)


@pytest.mark.os_agnostic
def test_a_free_slot_with_a_read_connector_is_found() -> None:
    slot = free_slot_for(Seat(link=CARD, port=NARROW, port_address="0000:00:02.0"), _inventory(connector=True))
    assert slot is not None and slot.address == "0000:00:03.0"


@pytest.mark.os_agnostic
def test_an_unread_connector_is_never_a_move_target() -> None:
    assert free_slot_for(Seat(link=CARD, port=NARROW, port_address="0000:00:02.0"), _inventory(connector=None)) is None


@pytest.mark.os_agnostic
def test_a_controller_seat_carries_its_link_its_port_and_the_port_address() -> None:
    controller = Controller(address="0000:01:00.0", name="hba", link=CARD, upstream=NARROW, upstream_address="0000:00:02.0")
    assert seat_of(controller) == Seat(link=CARD, port=NARROW, port_address="0000:00:02.0")
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_placement.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'lsdsk.domain.placement'`.

- [ ] **Step 3: Create `src/lsdsk/domain/placement.py`**

Move the bodies of `_achievable_pcie`, `_gain_in`, `_best_slot`, `_free_slot_for`, `_lower`, `_lower_int` from `diagnostics.py`, keeping their comments, re-keyed on a `Seat`:

```python
"""Where a card could sit: the one search for a better seat.

Asked by the storage rules for a controller and by the fabric-link rules for any
other card, so both are held to the same evidence: a slot is a move target only
where its connector bit was read, and an unread end is never a capable one.

System Role:
    Domain layer. Pure: reads the inventory's ports, returns one of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DomainModel
from .models import PcieLink, pcie_bandwidth_gbps
from .text import OptionalDeviceText

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import Controller, Inventory, PcieSlot


class Seat(DomainModel, frozen=True):
    """Where a card sits: its own link registers and the port it sits behind.

    Attributes:
        link: The card's own link registers.
        port: The port's link registers, or ``None`` where the port was never
            identified.
        port_address: The port's PCI address, which a search for a better seat
            never offers back.
    """

    link: PcieLink
    port: PcieLink | None = None
    port_address: OptionalDeviceText = None


def seat_of(controller: Controller) -> Seat:
    """The seat a storage controller sits in.

    Args:
        controller: The controller.

    Returns:
        Its link, the port above it, and that port's address.
    """
    return Seat(link=controller.link, port=controller.upstream, port_address=controller.upstream_address)


def achievable_pcie(seat: Seat) -> tuple[float | None, int | None]:
    """Return what the port a card currently sits in can give it.

    This is the *current* seat, not the best the board can offer; the two
    differ whenever a faster port exists but is occupied.

    Args:
        seat: The card and its port.

    Returns:
        The speed and width both ends support, or ``(None, None)`` when either
        end's capability was not read.
    """
    own_speed = seat.link.max_speed_gtps
    own_width = seat.link.max_width
    upstream = seat.port
    # BOTH ends or nothing. Taking the device's own maximum when the port was not
    # read makes an unmeasured port look at least as fast as the device, which
    # turns "not measured" into "the port is fine" and bills the shortfall to a
    # cable. Measured: a Gen5 drive in a Gen4 socket, on a platform that exposes
    # no link properties for PCIe bridges at all, was reported as a negotiation
    # fault with advice to reseat it.
    if own_speed is None or own_width is None:
        return None, None
    if upstream is None or upstream.max_speed_gtps is None or upstream.max_width is None:
        return None, None
    return min(own_speed, upstream.max_speed_gtps), min(own_width, upstream.max_width)


def _lower(left: float | None, right: float | None) -> float | None:
    """Return the smaller of two optional numbers, ignoring unknowns."""
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _lower_int(left: int | None, right: int | None) -> int | None:
    """Return the smaller of two optional integers, ignoring unknowns."""
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _gain_in(slot: PcieSlot, seat: Seat) -> float | None:
    """What a card would get in one slot, in GB/s.

    A slot that does not report its own speed and width is never a candidate.
    Legacy PCI bridges report neither, and treating an unknown capability as an
    unlimited one made every such bridge look like the fastest slot in the
    machine, which produced a confident recommendation to move a card into a
    slot slower than the one it already occupied.
    """
    if slot.link.max_speed_gtps is None or slot.link.max_width is None:
        return None
    return pcie_bandwidth_gbps(
        _lower(slot.link.max_speed_gtps, seat.link.max_speed_gtps),
        _lower_int(slot.link.max_width, seat.link.max_width),
    )


def best_slot(seat: Seat, inventory: Inventory, candidates: Callable[[PcieSlot], bool]) -> PcieSlot | None:
    """Find the slot passing a test that would serve a card best.

    Args:
        seat: The card and the port it sits behind now.
        inventory: The machine whose ports are searched.
        candidates: The search's own test of a port.

    Returns:
        The admitted port that would give the card the most, or ``None`` when
        none beats its current seat or that seat's figure was not read.
    """
    current = pcie_bandwidth_gbps(*achievable_pcie(seat))
    if current is None:
        return None
    best: PcieSlot | None = None
    best_bandwidth = current
    # The port this card already sits behind is skipped by the address it
    # records for that port. Matching the port's OCCUPANT instead missed it
    # whenever a sibling represented the port, and then offered the card the
    # seat it is already in as somewhere better to be. The machine hands over
    # one port per group of interchangeable ones rather than every port, which
    # is what keeps this search from costing the whole port list per card.
    for slot in inventory.placement_candidates(besides=seat.port_address, admits=candidates):
        gain = _gain_in(slot, seat)
        if gain is not None and gain > best_bandwidth:
            best, best_bandwidth = slot, gain
    return best


def free_slot_for(seat: Seat, inventory: Inventory) -> PcieSlot | None:
    """Find an empty slot with a real connector that would serve a card better.

    Args:
        seat: The card and the port it sits behind now.
        inventory: The machine whose ports are searched.

    Returns:
        The best free slot, or ``None``.
    """
    return best_slot(seat, inventory, lambda slot: slot.is_move_target)


__all__ = ["Seat", "achievable_pcie", "best_slot", "free_slot_for", "seat_of"]
```

- [ ] **Step 4: Rewire `diagnostics.py`**

Delete `_achievable_pcie`, `_gain_in`, `_best_slot`, `_free_slot_for`, `_lower`, `_lower_int`. Add:

```python
from .placement import achievable_pcie, best_slot, free_slot_for, seat_of
```

Replace call sites mechanically, then read every changed line:

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("src/lsdsk/domain/diagnostics.py"); t = p.read_text(encoding="utf-8")
for old, new in (
    ("_achievable_pcie(controller)", "achievable_pcie(seat_of(controller))"),
    ("_free_slot_for(controller, inventory)", "free_slot_for(seat_of(controller), inventory)"),
    ("_best_slot(controller, inventory, ", "best_slot(seat_of(controller), inventory, "),
):
    print(old, t.count(old)); t = t.replace(old, new)
for gone in ("_achievable_pcie", "_free_slot_for", "_best_slot(", "_gain_in", "_lower("):
    assert gone not in t, gone
p.write_text(t, encoding="utf-8")
EOF
git diff --stat src/lsdsk/domain/diagnostics.py
```

`_swap_slot_for` keeps its name and body; its last line becomes `return best_slot(seat_of(controller), inventory, is_worthwhile)` through the replacement above.

- [ ] **Step 5: Prove the storage findings did not move**

Run the Task 0 snippet again, writing `findings_after.json` into the same directory (`S=$(cat /tmp/fabric-links-scratch-path)`), then `cmp "$S/findings_before.json" "$S/findings_after.json" && echo IDENTICAL`.
Expected: `IDENTICAL`. Anything else is a STOP.

- [ ] **Step 6: Run** `tests/test_placement.py tests/test_diagnostics.py tests/test_placement_index.py tests/test_port_allocation_index.py tests/test_integrated_function_floor.py tests/test_interface_census.py tests/test_data_architecture.py tests/test_exported_docstrings.py`, ruff, both pyright configs, lint-imports. Expected: pass.

- [ ] **Step 7: Commit** (`refactor(domain): one free-slot search, keyed on a Seat`)

---

### Task 3: Enumerate the links no storage rule grades

**Files:**
- Modify: `src/lsdsk/domain/models.py` (`PciNode`, after `is_port`)
- Create: `src/lsdsk/domain/fabric_links.py`
- Test: `tests/test_fabric_links.py` (new)

**Interfaces:**
- Produces:
  - `PciNode.faces_downstream -> bool` (property)
  - `class FabricLink(DomainModel, frozen=True)`: `port: PciNode`, `functions: tuple[PciNode, ...]`, property `card -> PciNode` (the lowest function address)
  - `fabric_links(inventory: Inventory) -> tuple[FabricLink, ...]`, ordered by card address

**STOP conditions:** a committed capture yields a link whose card is a root or downstream port.

- [ ] **Step 1: Write the failing tests** (`tests/test_fabric_links.py`)

```python
"""Hints for the PCIe links no storage rule grades.

Graded once per link, at the device behind a port that faces downstream, and
never where a storage rule already grades it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.domain.enums import PciPortKind
from lsdsk.domain.fabric_links import fabric_links
from lsdsk.domain.models import Controller, Disk, Inventory, PcieLink, PciNode

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _link(running: tuple[float, int], capable: tuple[float, int]) -> PcieLink:
    return PcieLink(
        current_speed_gtps=running[0], current_width=running[1], max_speed_gtps=capable[0], max_width=capable[1]
    )


ROOT = PciNode(address="0000:00", name="root bus")
PORT = PciNode(
    address="0000:00:02.0",
    name="root port",
    class_code=0x060400,
    parent_address="0000:00",
    port_kind=PciPortKind.ROOT,
    link=_link((8.0, 8), (8.0, 8)),
    pcie_capability_present=True,
)


def _card(address: str = "0000:01:00.0", **fields: Any) -> PciNode:
    defaults: dict[str, Any] = {
        "name": "a card",
        "class_code": 0x030000,
        "parent_address": PORT.address,
        "link": _link((8.0, 8), (8.0, 16)),
        "pcie_capability_present": True,
    }
    return PciNode(address=address, **(defaults | fields))


@pytest.mark.os_agnostic
def test_only_root_and_downstream_ports_face_downstream() -> None:
    facing = {kind: PciNode(address="a", name="b", port_kind=kind).faces_downstream for kind in PciPortKind}
    assert facing == {
        PciPortKind.ROOT: True,
        PciPortKind.SWITCH_DOWNSTREAM: True,
        PciPortKind.SWITCH_UPSTREAM: False,
        PciPortKind.UNKNOWN: False,
    }


@pytest.mark.os_agnostic
def test_the_functions_of_one_device_are_one_link_at_its_lowest_function() -> None:
    tree = (ROOT, PORT, _card("0000:01:00.1"), _card("0000:01:00.0"))
    links = fabric_links(Inventory(hostname="h", pci_tree=tree))
    assert [(link.port.address, link.card.address, len(link.functions)) for link in links] == [
        ("0000:00:02.0", "0000:01:00.0", 2)
    ]


@pytest.mark.os_agnostic
def test_a_device_on_a_root_bus_has_no_link_to_grade() -> None:
    assert fabric_links(Inventory(hostname="h", pci_tree=(ROOT, PORT))) == ()


@pytest.mark.os_agnostic
def test_a_device_below_a_port_that_does_not_face_downstream_is_not_paired_with_it() -> None:
    upstream = PORT.with_changes(port_kind=PciPortKind.SWITCH_UPSTREAM)
    assert fabric_links(Inventory(hostname="h", pci_tree=(ROOT, upstream, _card()))) == ()


@pytest.mark.os_agnostic
def test_a_storage_controller_and_a_drive_host_are_left_to_their_own_rules() -> None:
    controller = Controller(address="0000:01:00.0", name="hba")
    host = _card("0000:02:00.0")
    tree = (ROOT, PORT, _card(), host)
    inventory = Inventory(
        hostname="h",
        pci_tree=tree,
        controllers=(controller,),
        disks=(Disk(path="/dev/sdz", node="sdz", model="m", controller_address=host.address),),
    )
    assert fabric_links(inventory) == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(path.stem for path in FIXTURES.glob("*.json")))
def test_no_committed_capture_grades_a_port_as_the_card_end(host: str) -> None:
    for link in fabric_links(_machine(host)):
        assert not link.card.faces_downstream, f"{host}: {link.card.address}"
        assert link.port.faces_downstream, f"{host}: {link.port.address}"
```

If `Disk(...)` refuses those fields, build it the way `tests/test_diagnostics.py` builds a minimal disk (read its `_disk` helper) - the point is a disk whose `controller_address` is the host's address.

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_fabric_links.py`
Expected: FAIL at collection, `No module named 'lsdsk.domain.fabric_links'`.

- [ ] **Step 3: Add `PciNode.faces_downstream` in `models.py`, after `is_port`**

```python
    @property
    def faces_downstream(self) -> bool:
        """Whether this node's link registers describe the link BELOW it.

        A root port and a switch's downstream port publish the link to the
        device behind them; every other node publishes the link to the port
        above it. So a device's link is paired with its parent only where the
        parent faces downstream: paired with a switch's UPSTREAM port, it would
        be read against the link that switch has to its own parent.

        Example:
            >>> PciNode(address="a", name="b", port_kind=PciPortKind.ROOT).faces_downstream
            True
            >>> PciNode(address="a", name="b", port_kind=PciPortKind.SWITCH_UPSTREAM).faces_downstream
            False
        """
        return self.port_kind in (PciPortKind.ROOT, PciPortKind.SWITCH_DOWNSTREAM)
```

- [ ] **Step 4: Create `src/lsdsk/domain/fabric_links.py` with the enumeration**

```python
"""Hints for the PCIe links no storage rule grades.

The storage rules grade a controller's link with the drives behind it in mind.
Every other card - a graphics card, a network card, a switch carrying either -
was drawn in the topology and judged nowhere, so a card that lost lanes or sits
in a slot narrower than itself went unremarked. These rules grade them, as
hints: nothing here costs a drive anything.

A speed-only shortfall is deliberately not graded. A graphics card lowers its
link speed while idle and retrains under load, so one reading cannot tell power
saving from a fault; width and capability do not move with load.

System Role:
    Domain layer. Pure: reads the inventory, returns findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DomainModel
from .models import PciNode

if TYPE_CHECKING:
    from .models import Inventory


class FabricLink(DomainModel, frozen=True):
    """One PCIe link no storage rule grades: a port and the device behind it.

    Attributes:
        port: The port the link leaves from, which faces downstream.
        functions: Every function of the device behind it, which share the link.
    """

    port: PciNode
    functions: tuple[PciNode, ...]

    @property
    def card(self) -> PciNode:
        """The function whose registers stand for the link: the lowest address."""
        return min(self.functions, key=lambda node: node.address)


def _device_of(address: str) -> str:
    """The bus:device part of an address, which every function of it shares."""
    return address.rpartition(".")[0]


def _graded_elsewhere(inventory: Inventory) -> frozenset[str]:
    """Addresses a storage rule already grades: controllers, and what a drive hangs off."""
    hosts = {disk.controller_address for disk in inventory.disks if disk.controller_address is not None}
    return frozenset({controller.address for controller in inventory.controllers} | hosts)


def fabric_links(inventory: Inventory) -> tuple[FabricLink, ...]:
    """Every link these rules grade, one per device behind a downstream-facing port.

    Args:
        inventory: The machine.

    Returns:
        The links, ordered by the address of the device behind each port.
    """
    by_address = {node.address: node for node in inventory.pci_tree}
    grouped: dict[tuple[str, str], list[PciNode]] = {}
    for node in inventory.pci_tree:
        port = by_address.get(node.parent_address) if node.parent_address is not None else None
        if port is None or not port.faces_downstream:
            continue
        grouped.setdefault((port.address, _device_of(node.address)), []).append(node)
    skipped = _graded_elsewhere(inventory)
    links = [
        FabricLink(port=by_address[port_address], functions=tuple(functions))
        for (port_address, _device), functions in grouped.items()
        if not any(function.address in skipped for function in functions)
    ]
    return tuple(sorted(links, key=lambda link: link.card.address))


__all__ = ["FabricLink", "fabric_links"]
```

- [ ] **Step 5: Run** `tests/test_fabric_links.py` plus `--doctest-modules src/lsdsk/domain/models.py`. Expected: PASS.
- [ ] **Step 6: Mutation check:** change `faces_downstream` to return `self.is_port`, run `tests/test_fabric_links.py`, require a FAIL naming `test_only_root_and_downstream_ports_face_downstream` or the capture sweep; restore from a copy made before the mutation (`cp` the file aside first), never from git.
- [ ] **Step 7: Commit** (`feat(domain): enumerate the PCIe links no storage rule grades`)

---

### Task 4: Say what a bridge or switch carries

**Files:**
- Modify: `src/lsdsk/domain/fabric_links.py`
- Test: `tests/test_fabric_links.py`

**Interfaces:**
- Produces: `carrying_clause(link: FabricLink, tree: Sequence[PciNode]) -> str` - `""` for a plain card, otherwise `", carrying <names>"`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from lsdsk.domain.fabric_links import FabricLink, carrying_clause  # move to the top import block


def _below(parent: str, address: str, name: str, class_code: int = 0x030000) -> PciNode:
    return PciNode(address=address, name=name, class_code=class_code, parent_address=parent)


SWITCH = _card(name="switch", class_code=0x060400, port_kind=PciPortKind.SWITCH_UPSTREAM)
LEG_A = _below(SWITCH.address, "0000:02:08.0", "leg", 0x060400)
LEG_B = _below(SWITCH.address, "0000:02:10.0", "leg", 0x060400)


@pytest.mark.os_agnostic
def test_a_plain_card_carries_nothing() -> None:
    assert carrying_clause(FabricLink(port=PORT, functions=(_card(),)), (ROOT, PORT, _card())) == ""


@pytest.mark.os_agnostic
def test_a_switch_names_the_devices_behind_it_grouped_and_counted() -> None:
    tree = (
        ROOT, PORT, SWITCH, LEG_A, LEG_B,
        _below(LEG_A.address, "0000:03:00.0", "GPU"),
        _below(LEG_A.address, "0000:03:00.1", "GPU audio", 0x040300),
        _below(LEG_B.address, "0000:04:00.0", "GPU"),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), tree) == ", carrying 2x GPU"


@pytest.mark.os_agnostic
def test_more_than_two_names_end_in_a_count() -> None:
    tree = (
        ROOT, PORT, SWITCH, LEG_A,
        *(_below(LEG_A.address, f"0000:0{i}:00.0", f"device {i}") for i in range(3, 7)),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), tree) == (
        ", carrying device 3, device 4 and 2 more"
    )


@pytest.mark.os_agnostic
def test_every_function_of_a_bridge_card_contributes_what_it_carries() -> None:
    first = _card("0000:01:00.0", name="bridge", class_code=0x060400)
    second = _card("0000:01:00.2", name="bridge", class_code=0x060400)
    tree = (ROOT, PORT, first, second, _below(first.address, "0000:02:04.0", "NIC"), _below(second.address, "0000:03:05.0", "NIC"))
    assert carrying_clause(FabricLink(port=PORT, functions=(first, second)), tree) == ", carrying 2x NIC"
```

- [ ] **Step 2: Run them.** Expected: FAIL, `ImportError: cannot import name 'carrying_clause'`.

- [ ] **Step 3: Implement** (add to `fabric_links.py`; `from collections import Counter` and, under `TYPE_CHECKING`, `from collections.abc import Sequence`)

```python
#: How many distinct names a carrying clause spells out before it counts the rest.
_NAMES_SPELLED_OUT = 2


def _is_function_zero(node: PciNode) -> bool:
    """Whether a node is a device's first function, which stands for the device."""
    return node.address.rpartition(".")[2].partition("#")[0] == "0"


def _devices_below(link: FabricLink, tree: Sequence[PciNode]) -> list[PciNode]:
    """The end devices below the card, walked from an explicit stack, first functions only."""
    children: dict[str, list[PciNode]] = {}
    for node in tree:
        if node.parent_address is not None:
            children.setdefault(node.parent_address, []).append(node)
    found: list[PciNode] = []
    stack = [function.address for function in link.functions]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child.is_bridge_family:
                stack.append(child.address)
            elif _is_function_zero(child):
                found.append(child)
    return sorted(found, key=lambda node: node.address)


def carrying_clause(link: FabricLink, tree: Sequence[PciNode]) -> str:
    """What a bridge or switch card carries, for the title of its finding.

    A switch or a bridge chip is the device at the card end of the link, and its
    name is one a reader has never seen on the box: the HD 7990 reads as a PLX
    switch. So the title names the end devices behind it, grouped by name.

    Args:
        link: The link whose card end is described.
        tree: The machine's whole PCI tree.

    Returns:
        ``""`` for a card with nothing behind it, otherwise ``", carrying "``
        and the names, at most two spelled out and the rest counted.
    """
    counted = Counter(node.name for node in _devices_below(link, tree))
    if not counted:
        return ""
    names = [f"{count}x {name}" if count > 1 else name for name, count in counted.items()]
    spelled = ", ".join(names[:_NAMES_SPELLED_OUT])
    rest = len(names) - _NAMES_SPELLED_OUT
    return f", carrying {spelled}" + (f" and {rest} more" if rest > 0 else "")
```

Add `"carrying_clause"` to `__all__`.

- [ ] **Step 4: Run** the file's tests. Expected: PASS. Then ruff format (it reflows the tuples above).
- [ ] **Step 5: Commit** (`feat(domain): name what a bridge or switch card carries`)

---

### Task 5: The two hints, and where the card could go

**Files:**
- Modify: `src/lsdsk/domain/fabric_links.py`, `src/lsdsk/domain/diagnostics.py` (`diagnose`)
- Test: `tests/test_fabric_links.py`

**Interfaces:**
- Consumes: `Seat`, `free_slot_for` (Task 2); `format_gbytes`, `format_pcie_sentence` (Task 1); `fabric_links`, `carrying_clause` (Tasks 3-4).
- Produces: `diagnose_fabric_link(link: FabricLink, inventory: Inventory) -> list[Finding]`, `diagnose_fabric_links(inventory: Inventory) -> list[Finding]`.

**STOP conditions:** the per-capture table in Step 1 does not match what the code produces AND the difference is not explained by a fact this plan got wrong (then report it, do not edit the table to agree).

- [ ] **Step 1: Write the failing tests** (append)

```python
from lsdsk.domain.diagnostics import diagnose  # top import block
from lsdsk.domain.enums import Severity  # top import block
from lsdsk.domain.fabric_links import diagnose_fabric_link, diagnose_fabric_links  # top import block
from lsdsk.domain.models import PcieSlot  # top import block

LANES = "runs on fewer lanes than both ends support"
CAPPED = "is capped by its slot"

#: (capture, card address, which hint) for every hint the committed captures raise.
EXPECTED: tuple[tuple[str, str, str], ...] = (
    ("linux-minimal", "0000:01:00.0", CAPPED),
    ("linux-nvme-board", "0000:01:00.0", CAPPED),
    ("linux-sas-hba", "0000:01:00.0", CAPPED),
    ("linux-sas-hba-later", "0000:01:00.0", CAPPED),
    ("linux-usb-ehci", "0000:01:00.0", LANES),
    ("linux-usb-ehci", "0000:04:00.0", CAPPED),
)


def _hints(inventory: Inventory) -> list[tuple[str, str]]:
    return [
        (finding.subject, LANES if LANES in finding.title else CAPPED)
        for finding in diagnose_fabric_links(inventory)
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(path.stem for path in FIXTURES.glob("*.json")))
def test_every_committed_capture_raises_exactly_the_hints_measured_for_it(host: str) -> None:
    expected = [(card, kind) for capture, card, kind in EXPECTED if capture == host]
    assert _hints(_machine(host)) == expected


@pytest.mark.os_agnostic
def test_every_fabric_hint_is_a_hint_and_reaches_the_findings() -> None:
    machine = _machine("linux-usb-ehci")
    ours = [finding for finding in diagnose(machine) if LANES in finding.title or CAPPED in finding.title]
    assert [finding.subject for finding in ours] == ["0000:01:00.0", "0000:04:00.0"]
    assert {finding.severity for finding in ours} == {Severity.HINT}


@pytest.mark.os_agnostic
def test_the_hd_7990_is_sent_to_the_free_x16_slot() -> None:
    capped = next(f for f in diagnose_fabric_links(_machine("linux-usb-ehci")) if f.subject == "0000:04:00.0")
    assert "carrying 2x" in capped.title and "Radeon HD 7990" in capped.title
    assert "PCIe Gen3x16" in capped.detail and "0000:00:02.2" in capped.detail
    assert capped.action is not None and capped.action.startswith("Move it to the free slot at 0000:00:03.0 (PCIe Gen3x16")


@pytest.mark.os_agnostic
def test_lanes_lost_names_how_many_did_not_train() -> None:
    lost = next(f for f in diagnose_fabric_links(_machine("linux-usb-ehci")) if f.subject == "0000:01:00.0")
    assert "Running PCIe Gen1x4 (1.00 GB/s)" in lost.detail
    assert "both support PCIe Gen1x8 (2.00 GB/s): 4 of 8 lanes did not train" in lost.detail


def _graded(card: PciNode, *, slots: tuple[PcieSlot, ...] = ()) -> list[Any]:
    tree = (ROOT, PORT, card)
    inventory = Inventory(hostname="h", pci_tree=tree, slots=slots)
    return [diagnose_fabric_link(link, inventory) for link in fabric_links(inventory)][0]


@pytest.mark.os_agnostic
def test_a_speed_only_shortfall_raises_nothing() -> None:
    idle = _card(link=_link((2.5, 8), (8.0, 8)))
    assert _graded(idle) == []


@pytest.mark.os_agnostic
def test_a_link_that_lost_lanes_is_not_also_called_capped() -> None:
    both = _card(link=_link((8.0, 4), (8.0, 16)))
    titles = [finding.title for finding in _graded(both)]
    assert len(titles) == 1 and LANES in titles[0]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("unread", ["card", "port"])
def test_an_unread_end_grades_nothing(unread: str) -> None:
    card = _card(link=PcieLink(current_speed_gtps=8.0, current_width=8)) if unread == "card" else _card()
    port = PORT.with_changes(link=PcieLink(current_speed_gtps=8.0, current_width=8)) if unread == "port" else PORT
    inventory = Inventory(hostname="h", pci_tree=(ROOT, port, card))
    assert diagnose_fabric_links(inventory) == []


@pytest.mark.os_agnostic
def test_a_link_that_never_trained_is_left_to_the_none_marker() -> None:
    assert _graded(_card(link=_link((2.5, 0), (8.0, 16)))) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("connector", "advice"),
    [
        (True, "Move it to the free slot at 0000:00:03.0 (PCIe Gen3x16, slot 4)."),
        (False, "No free slot on this board would carry more"),
        (None, "Whether a free slot would carry more was not readable"),
    ],
    ids=["free-slot", "no-free-slot", "connector-unread"],
)
def test_a_capped_card_is_told_where_it_could_go_only_on_a_read_connector(connector: bool | None, advice: str) -> None:
    wide = PcieSlot(
        address="0000:00:03.0", link=PcieLink(max_speed_gtps=8.0, max_width=16), connector_present=connector,
        physical_slot_number=4,
    )
    here = PcieSlot(address=PORT.address, link=PORT.link, occupied=True, connector_present=connector)
    capped = _graded(_card(), slots=(here, wide))
    assert len(capped) == 1 and CAPPED in capped[0].title
    assert capped[0].action is not None and capped[0].action.startswith(advice)
```

- [ ] **Step 2: Run them.** Expected: FAIL, `ImportError: cannot import name 'diagnose_fabric_link'`.

- [ ] **Step 3: Implement** (add to `fabric_links.py`; imports: `from typing import NamedTuple`, `from .enums import Severity`, `from .models import Finding, PciNode, pcie_bandwidth_gbps`, `from .pcie_text import format_gbytes, format_pcie_sentence`, `from .placement import Seat, free_slot_for`)

```python
class _Shape(NamedTuple):
    """A link's generation and width, both read."""

    speed_gtps: float
    width: int


def _both_support(link: FabricLink) -> _Shape | None:
    """What both ends of a link support, or ``None`` when either end was not read."""
    card, port = link.card.link, link.port.link
    if card.max_speed_gtps is None or card.max_width is None:
        return None
    if port.max_speed_gtps is None or port.max_width is None:
        return None
    return _Shape(min(card.max_speed_gtps, port.max_speed_gtps), min(card.max_width, port.max_width))


def diagnose_fabric_link(link: FabricLink, inventory: Inventory) -> list[Finding]:
    """Grade one link: lanes lost first, then a slot that caps the card.

    Args:
        link: The link.
        inventory: The machine, for the tree the title names and the free slots.

    Returns:
        At most one hint; none where either end's capability or the running
        width was not read, where the link never trained, or where the only
        shortfall is speed.
    """
    achievable = _both_support(link)
    running_width = link.card.link.current_width
    if achievable is None or running_width is None or running_width == 0:
        return []
    name = f"{link.card.name}{carrying_clause(link, inventory.pci_tree)}"
    if running_width < achievable.width:
        return [_lanes_lost(link, name=name, achievable=achievable, running_width=running_width)]
    capped = pcie_bandwidth_gbps(achievable.speed_gtps, achievable.width)
    own = link.card.link.max_bandwidth_gbps
    if capped is None or own is None or capped >= own:
        return []
    return [_slot_capped(link, name=name, achievable=achievable, inventory=inventory)]


def _lanes_lost(link: FabricLink, *, name: str, achievable: _Shape, running_width: int) -> Finding:
    """The hint for a link running narrower than both of its ends support."""
    running = link.card.link
    return Finding(
        severity=Severity.HINT,
        subject=link.card.address,
        title=f"{name} runs on fewer lanes than both ends support",
        detail=(
            f"Running {format_pcie_sentence(running.current_speed_gtps, running_width)} "
            f"({format_gbytes(running.current_bandwidth_gbps)}) where the card and the port at "
            f"{link.port.address} both support {format_pcie_sentence(achievable.speed_gtps, achievable.width)} "
            f"({format_gbytes(pcie_bandwidth_gbps(achievable.speed_gtps, achievable.width))}): "
            f"{achievable.width - running_width} of {achievable.width} lanes did not train."
        ),
        action="Reseat the card and check the slot and any riser; a lane that does not train is usually a contact.",
    )


def _slot_capped(link: FabricLink, *, name: str, achievable: _Shape, inventory: Inventory) -> Finding:
    """The hint for a card its slot holds below what the card can do."""
    card, port = link.card.link, link.port.link
    return Finding(
        severity=Severity.HINT,
        subject=link.card.address,
        title=f"{name} is capped by its slot",
        detail=(
            f"The card can do {format_pcie_sentence(card.max_speed_gtps, card.max_width)} "
            f"({format_gbytes(card.max_bandwidth_gbps)}); the port at {link.port.address} offers "
            f"{format_pcie_sentence(port.max_speed_gtps, port.max_width)} ({format_gbytes(port.max_bandwidth_gbps)}), "
            f"which caps it at {format_pcie_sentence(achievable.speed_gtps, achievable.width)}."
        ),
        action=_where_it_could_go(link, inventory),
    )


def _where_it_could_go(link: FabricLink, inventory: Inventory) -> str:
    """Name a free slot that would carry more, or say why none is named."""
    seat = Seat(link=link.card.link, port=link.port.link, port_address=link.port.address)
    free = free_slot_for(seat, inventory)
    if free is not None:
        number = "" if free.physical_slot_number is None else f", slot {free.physical_slot_number}"
        figure = format_pcie_sentence(free.link.max_speed_gtps, free.link.max_width)
        return (
            f"Move it to the free slot at {free.address} ({figure}{number}). "
            "Check the slot is mechanically long enough or open-ended first."
        )
    # A connector bit nobody read is not a missing slot: without root no port
    # is known to end in one, and "no free slot" would claim what was not seen.
    if not any(slot.connector_present is not None for slot in inventory.slots):
        return (
            "Whether a free slot would carry more was not readable: telling a slot from an internal port "
            "needs the PCIe capability, which takes root to read."
        )
    return "No free slot on this board would carry more; only a board with a faster port would."


def diagnose_fabric_links(inventory: Inventory) -> list[Finding]:
    """Grade every link no storage rule grades.

    Args:
        inventory: The machine.

    Returns:
        One hint per link that lost lanes or is capped by its slot.
    """
    return [finding for link in fabric_links(inventory) for finding in diagnose_fabric_link(link, inventory)]
```

Add `"diagnose_fabric_link"`, `"diagnose_fabric_links"` to `__all__`.

In `diagnostics.py`, import `from .fabric_links import diagnose_fabric_links` and extend `diagnose()`:

```python
    if physical:
        findings.extend(diagnose_port_allocation(inventory))
        findings.extend(diagnose_fabric_links(inventory))
```

- [ ] **Step 4: Run** `tests/test_fabric_links.py`. Expected: PASS. If the capture table disagrees, print `_hints(_machine(host))` for that host and check the difference against the design's measured table before touching anything (STOP condition).
- [ ] **Step 5: Mutation checks**, each from a copy made aside first, restored from that copy, each required to turn a NAMED test red:
  - drop the `running_width < achievable.width` branch -> `test_lanes_lost_names_how_many_did_not_train` and the usb-ehci table row;
  - delete the connector-unread branch -> `...[connector-unread]`;
  - test the slot cap BEFORE the lanes check, so a link that lost lanes on a capped card is called capped -> `test_a_link_that_lost_lanes_is_not_also_called_capped`;
  - delete the `running_width == 0` guard -> `test_a_link_that_never_trained_is_left_to_the_none_marker`;
  - remove `if node.parent_address ...faces_downstream` from `fabric_links` -> `test_no_committed_capture_grades_a_port_as_the_card_end`.
- [ ] **Step 6: Run the whole suite in the foreground** (`-m "not local_only"`). Expected: green. Tests that count a capture's findings (`test_refused_readings`, the quoted-output blocks, `test_cli_exit_codes`) may move by the new hints: a quoted block that now prints a new finding line is re-quoted from the registered command's own output, never hand-edited; an exit code must NOT move (hints exit 0) - if one does, STOP.
- [ ] **Step 7: Commit** (`feat(diagnostics): hint where a non-storage PCIe link loses lanes or its slot caps the card`)

---

### Task 6: Documents

**Files:**
- Modify: `FINDINGS.md` and `de/FINDINGS.md` ("What it finds" list), `de/TRANSLATIONS.toml` (by its script), `CHANGELOG.md` (the 1.6.0 section)
- Modify via `bitranox:meta-skill-writer`: `skills/lsdsk/SKILL.md` (wherever it lists what the findings are about), plus its `.skillwriter/checklist-2026-10-02-fabric-link-hints.md`

- [ ] **Step 1: FINDINGS.md** - after the bullet "A controller capped by the mainboard, ...", add:

```markdown
- Any other PCIe card - a graphics card, a network card, a switch carrying either - whose link runs on
  fewer lanes than both ends support, or whose slot caps it below what the card can do, as a hint naming
  a free slot that would carry more. A link that is only running SLOWER is not reported: graphics cards
  lower their link speed while idle.
```

`de/FINDINGS.md`, at the matching bullet:

```markdown
- Jede andere PCIe-Karte - eine Grafikkarte, eine Netzwerkkarte, ein Switch, der eine davon trägt -,
  deren Link auf weniger Lanes läuft, als beide Enden unterstützen, oder deren Steckplatz sie unter das
  drosselt, was die Karte kann, als Hinweis mit einem freien Steckplatz, der mehr tragen würde. Ein Link,
  der nur LANGSAMER läuft, wird nicht gemeldet: Grafikkarten senken ihre Link-Geschwindigkeit im Leerlauf.
```

Then `.venv/bin/python scripts/translation_manifest.py --refresh FINDINGS.md`.

- [ ] **Step 2: CHANGELOG.md** - under the unreleased 1.6.0 heading, in its Added list:

```markdown
- Hints for non-storage PCIe links: a card whose link runs on fewer lanes than both ends support, or whose
  slot caps it, is reported as a hint, with a free slot that would carry more where one exists.
```

and under Fixed:

```markdown
- The topology tree draws a heading for every root complex, so a second root complex no longer
  continues in a column the first one had closed.
```

- [ ] **Step 3: SKILL.md** through `bitranox:meta-skill-writer` (RED, GREEN, review artifact), wherever the skill enumerates what lsdsk reports; run every test that names `SKILL.md` (`grep -l SKILL.md tests/*.py`).
- [ ] **Step 4: Run** `tests/test_translations.py tests/test_quoted_output_is_reproduced.py` and the SKILL tests. Expected: PASS.
- [ ] **Step 5: Commit** each document change (docs, then the skill with its artifact).

---

After Task 6, the 1.6.0 release (Task 12 of `docs/plans/2026-10-01-usb-link.md`) resumes: docs pass, `make bump-minor`, `make test`, the real-hardware suite, then ASK before merging to main and pushing.
