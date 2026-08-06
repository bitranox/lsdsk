"""What history changes about the verdict, and what it must leave alone.

The two captures in ``tests/fixtures/hw`` are the same machine 15 to 16 power-on
hours apart, so these run against measured hardware. Two of its drives carry
comparable CRC totals and could not be more different: one gained sixteen
thousand errors between the captures, the other gained none. Before history the
tool reported them identically, with the same severity and the same sentence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose, refine
from lsdsk.domain.enums import Severity
from lsdsk.domain.history import CounterKind, History, Trend, TrendVerdict, record
from lsdsk.domain.models import Finding

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"

T0 = "2026-08-05T01:23:00+00:00"
T1 = "2026-08-05T17:03:00+00:00"

# Both are Samsung SSD 870 EVO 4TB on the same host, with comparable totals.
LIVE_FAULT = "/dev/sdd"  # 2196127 CRC errors, gaining about 1100 an hour
DEAD_FAULT = "/dev/sdj"  # 462640 CRC errors, none of them recent


def load(host: str) -> dict[str, object]:
    """Read one hardware capture."""
    with (FIXTURE_DIR / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, object] = json.load(handle)
    return payload


def latest_inventory() -> Inventory:
    """The machine as the later capture saw it."""
    return build_from(load("linux-sas-hba-later"))


def paired_history() -> History:
    """History built from both real captures, oldest first."""
    history = History(hostname="linux-sas-hba")
    history = record(history, build_from(load("linux-sas-hba")).disks, T0)
    return record(history, latest_inventory().disks, T1)


def crc_finding(findings: tuple[Finding, ...], subject: str) -> Finding:
    """The interface CRC finding for one drive."""
    matches = [f for f in findings if f.subject == subject and "CRC" in f.title]
    assert len(matches) == 1, f"expected exactly one CRC finding for {subject}, got {len(matches)}"
    return matches[0]


# --------------------------------------------------------------------------
# The regression guard: no history means no change
# --------------------------------------------------------------------------


def test_without_history_every_finding_is_unchanged() -> None:
    """A first run, an unprivileged run and a replay must read as they always did."""
    inventory = latest_inventory()
    assert diagnose(inventory) == diagnose(inventory, history=None)
    assert diagnose(inventory, history=History(hostname="linux-sas-hba")) == diagnose(inventory)


def test_a_single_sample_changes_nothing() -> None:
    """One reading is not a trend, and must not move a severity."""
    inventory = latest_inventory()
    one = record(History(hostname="linux-sas-hba"), inventory.disks, T1)
    assert diagnose(inventory, history=one) == diagnose(inventory)


# --------------------------------------------------------------------------
# The two drives the tool could not tell apart
# --------------------------------------------------------------------------


def test_before_history_the_live_and_dead_faults_read_identically() -> None:
    """The baseline this whole feature exists to fix.

    If this ever stops holding, the comparison below stops meaning anything, so
    it is asserted rather than assumed.
    """
    findings = diagnose(latest_inventory())
    live, dead = crc_finding(findings, LIVE_FAULT), crc_finding(findings, DEAD_FAULT)
    assert live.severity is dead.severity
    assert live.action == dead.action


def test_a_live_fault_escalates_and_names_the_rate() -> None:
    findings = diagnose(latest_inventory(), history=paired_history())
    live = crc_finding(findings, LIVE_FAULT)
    assert live.severity is Severity.CRITICAL
    assert "power-on hours" in live.detail
    assert "happening now" in live.detail


def test_a_dead_fault_de_escalates_and_says_so() -> None:
    findings = diagnose(latest_inventory(), history=paired_history())
    dead = crc_finding(findings, DEAD_FAULT)
    assert dead.severity is Severity.HINT
    assert "not doing so now" in dead.detail
    assert dead.action is not None
    assert "replacing anything would fix a fault that is over" in dead.action


def test_the_two_are_now_told_apart() -> None:
    """The point of the exercise, stated as one assertion."""
    findings = diagnose(latest_inventory(), history=paired_history())
    assert crc_finding(findings, LIVE_FAULT).severity is not crc_finding(findings, DEAD_FAULT).severity


def test_a_drive_whose_span_proves_nothing_is_left_alone() -> None:
    """430 errors at 0.04 an hour: 15 quiet hours are not evidence of anything."""
    before = crc_finding(diagnose(latest_inventory()), "/dev/sde")
    after = crc_finding(diagnose(latest_inventory(), history=paired_history()), "/dev/sde")
    assert after.severity is before.severity
    assert after.detail == before.detail


# --------------------------------------------------------------------------
# Consequences worth stating out loud
# --------------------------------------------------------------------------


def test_severity_moves_by_one_step_at_most() -> None:
    """History refines a judgement the counters justified; it does not leap."""
    order = [Severity.HINT, Severity.WARNING, Severity.CRITICAL]
    plain = {(f.subject, f.title): f.severity for f in diagnose(latest_inventory())}
    for finding in diagnose(latest_inventory(), history=paired_history()):
        was = plain.get((finding.subject, finding.title))
        if was is not None:
            assert abs(order.index(finding.severity) - order.index(was)) <= 1


def test_history_never_invents_a_finding() -> None:
    """Every subject reported with history was already reported without it."""
    plain = {(f.subject, f.title) for f in diagnose(latest_inventory())}
    with_history = {(f.subject, f.title) for f in diagnose(latest_inventory(), history=paired_history())}
    assert with_history == plain


@pytest.mark.parametrize("subject", [LIVE_FAULT, DEAD_FAULT])
def test_the_detail_always_carries_the_measurement_it_acted_on(subject: str) -> None:
    """A severity that moved must say what moved it."""
    findings = diagnose(latest_inventory(), history=paired_history())
    detail = crc_finding(findings, subject).detail
    assert "power-on hours" in detail


# --------------------------------------------------------------------------
# "severity moves at most one step" - the whole mapping, not one entry
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("base", [Severity.HINT, Severity.WARNING, Severity.CRITICAL])
@pytest.mark.parametrize("verdict", [TrendVerdict.RISING, TrendVerdict.QUIET])
def test_a_trend_moves_a_severity_by_at_most_one_step(base: Severity, verdict: TrendVerdict) -> None:
    """CLAUDE.md states this rule and names this file as asserting it.

    It was asserted for exactly one of the three base severities. Instrumenting
    the maps showed only the ``WARNING`` entry was ever executed by the whole
    suite, and mutating ``_ESCALATION[HINT]`` to CRITICAL, the two-step jump the
    code comment expressly forbids, left all 884 tests green. Six cases, no
    fixture.
    """
    order = [Severity.HINT, Severity.WARNING, Severity.CRITICAL]
    trend = Trend(
        kind=CounterKind.CRC_ERRORS,
        verdict=verdict,
        latest=100,
        delta=50 if verdict is TrendVerdict.RISING else 0,
        span_hours=200,
        per_hour=0.25 if verdict is TrendVerdict.RISING else None,
        expected_from_lifetime=None if verdict is TrendVerdict.RISING else 50.0,
    )
    finding = Finding(severity=base, subject="/dev/sda", title="something", detail="", action=None)
    moved = refine(finding, trend)
    distance = abs(order.index(moved.severity) - order.index(base))
    assert distance <= 1, f"{base.value} + {verdict.value} moved {distance} steps to {moved.severity.value}"


@pytest.mark.os_agnostic
def test_without_a_trend_a_severity_never_moves() -> None:
    """The control: refine must be a no-op where there is nothing measured."""
    for base in (Severity.HINT, Severity.WARNING, Severity.CRITICAL):
        finding = Finding(severity=base, subject="/dev/sda", title="something", detail="", action=None)
        assert refine(finding, None).severity is base
