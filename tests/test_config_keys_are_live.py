"""Every documented configuration key must demonstrably change what lsdsk does.

The existing coverage test asserts that a key shipped in ``defaultconfig.d`` also
exists on its model. That is a weaker claim than it reads as, and the gap is not
theoretical: ``display.summary_limit`` satisfied it for months while being parsed
into ``DisplaySettings`` and passed to nothing, so ``--set display.summary_limit=2``
produced byte-identical output. ``display.wear_row_floor_percent`` was honoured by
one command out of ten. Two ``display`` temperature keys were read by nothing at
all, under a comment promising they changed severity and the exit code.

Three of the same defect, none of which "the key exists on a model" could see,
because *read* and *used* are different questions and only one was being asked.

This asks the other one, end to end and without a double anywhere: it runs the
real CLI in a real subprocess, twice per key with values far apart, and requires
the two runs to differ somewhere. A key that cannot move any output is either
dead or undocumented-as-inert, and both are findings.

The commands are driven against the committed captures rather than live hardware
so the result does not depend on the machine the suite runs on; the real-hardware
counterpart lives in ``tests/e2e``.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
DEFAULTS = Path(__file__).parent.parent / "src" / "lsdsk" / "adapters" / "config" / "defaultconfig.d"

#: Captures chosen for contrast: one machine with findings of several kinds, one
#: with almost none, and one whose drives carry recorded-counter history.
CAPTURES = ("linux-sas-hba.json", "linux-minimal.json", "linux-nvme-board.json")

#: The views a key could plausibly move. `findings` and the bare report between
#: them cover severity, the verdict line and every table.
COMMANDS: tuple[tuple[str, ...], ...] = (
    (),
    ("findings",),
    ("health",),
    ("trend",),
    ("slots",),
)

#: Values far enough apart that any key which is read at all should separate
#: them, plus any COMPANION settings needed to reach the key at all. A companion
#: is applied identically to both runs, so the difference stays attributable to
#: the key under test; it exists because several keys sit behind a gate another
#: key controls. `wear_critical_percent` is the clearest: `_wear_findings`
#: returns early when wear is under `wear_warning_percent`, and no captured drive
#: is over the shipped 80, so without lowering the gate the key has nothing to
#: grade and reads as dead when it is not.
PROBES: dict[str, tuple[str, str, dict[str, str]]] = {
    "crc_errors_significant": ("1", "100000000", {}),
    "min_span_hours": ("1", "100000", {}),
    "mixed_firmware_threshold": ("2", "1000", {}),
    "quiet_expected_min": ("0.0001", "100000.0", {}),
    # Reachable only once a wear finding exists at all.
    "wear_critical_percent": ("1", "100", {"thresholds.wear_warning_percent": "1"}),
    "wear_warning_percent": ("1", "100", {}),
    # The projection sentence needs a wear finding AND a measured rate, so it
    # needs both the gate lowered and a seeded history (see SEEDED).
    "wear_projection_min_points": ("1", "100000000", {"thresholds.wear_warning_percent": "1"}),
    "piped_width": ("60", "400", {}),
    "summary_limit": ("1", "1000", {}),
    "wear_row_floor_percent": ("0", "100", {}),
    "expand_virtual": ("false", "true", {}),
    "traceback_summary_limit": ("10", "100000", {}),
    "traceback_verbose_limit": ("10", "100000", {}),
    "max_samples_per_drive": ("2", "100000", {}),
    "enabled": ("true", "false", {}),
}

#: Keys whose effect this harness cannot observe, each with the reason and what
#: does cover it. Anything here is a deliberate exemption, not an oversight, and
#: a key may only be added with a reason that says why no run can show it.
UNOBSERVABLE: dict[str, str] = {
    "path": "names WHERE the store goes, not what any run renders; covered by test_history_config",
    "traceback_summary_limit": "only reachable on a raising command, which cli_fail covers in test_cli_core",
    "traceback_verbose_limit": "only reachable on a raising command, which cli_fail covers in test_cli_core",
    "max_samples_per_drive": "bounds a stored series, not a rendered one; covered by test_history_store",
    "enabled": "suppresses the write, which leaves rendering identical by design; covered by test_history_cli",
}


def documented_keys() -> list[tuple[str, str]]:
    """Every ``(section, key)`` shipped in a default configuration file."""
    found: list[tuple[str, str]] = []
    for toml in sorted(DEFAULTS.glob("*.toml")):
        parsed: dict[str, Any] = tomllib.loads(toml.read_text(encoding="utf-8"))
        for section in ("thresholds", "display", "history"):
            table = parsed.get(section)
            if isinstance(table, dict):
                keys = cast("dict[str, Any]", table)
                found += [(section, str(key)) for key in keys]
    return sorted(set(found))


#: Keys whose effect only appears once counters have a recorded past. The probe
#: seeds a store from two captures of the same machine taken hours apart, which
#: is the only way a rate exists to grade.
SEEDED = frozenset({"wear_projection_min_points", "quiet_expected_min", "min_span_hours", "wear_row_floor_percent"})


def _rising_wear_store(tmp_path: Path) -> Path:
    """A store whose wear RISES, which no committed capture pair does.

    Between the only two captures of one machine, wear moves `+0`, so
    `Trend.is_rising` is never true for it and the projection sentence cannot
    render however the threshold is set. A history file is user-supplied input by
    design, so writing one is supplying an input rather than substituting a
    double: it goes through the real writer and is read back by the real loader.
    """
    from lsdsk.adapters.history.store import save_history
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.domain.history import DiskSeries, History, Sample, identity_of

    machine = load(FIXTURES / "linux-sas-hba.json")
    disk = next(d for d in machine.disks if identity_of(d) and d.health and d.health.percent_used is not None)
    samples = tuple(
        Sample(power_on_hours=hour, captured_at=f"2024-0{1 + index}-01T00:00:00Z", percent_used=used)
        for index, (hour, used) in enumerate(((1000, 10), (2000, 40), (3000, 70)))
    )
    store = tmp_path / "rising-wear.json"
    save_history(
        History(
            hostname=machine.hostname,
            series=(DiskSeries(identity=identity_of(disk) or "", model=disk.model, samples=samples),),
        ),
        store,
    )
    return store


def _seeded_store(tmp_path: Path) -> Path:
    """A real history built by the real recorder from two real captures."""
    store = tmp_path / "seeded.json"
    for capture in ("linux-sas-hba.json", "linux-sas-hba-later.json"):
        subprocess.run(  # noqa: S603 - argv list, no shell
            [
                sys.executable,
                "-m",
                "lsdsk",
                "--history-file",
                str(store),
                "record",
                "--replay",
                str(FIXTURES / capture),
            ],
            capture_output=True,
            check=False,
        )
    return store


def _run(argv: list[str]) -> str:
    """One real lsdsk process, its output reduced to a digest."""
    result = subprocess.run(  # noqa: S603 - argv list, no shell, all values from this file
        [sys.executable, "-m", "lsdsk", *argv],
        capture_output=True,
        check=False,
    )
    return hashlib.sha256(result.stdout + b"|" + str(result.returncode).encode()).hexdigest()


def _moves_something(section: str, key: str, store: Path | None) -> bool:
    """Whether either probe value changes any command's output on any capture."""
    low, high, companions = PROBES[key]
    fixed: list[str] = []
    for name, value in companions.items():
        fixed += ["--set", f"{name}={value}"]
    if store is not None:
        fixed += ["--history-file", str(store)]
    for capture in CAPTURES:
        target = str(FIXTURES / capture)
        for command in COMMANDS:
            base = [*fixed, *command, "--replay", target]
            if _run(["--set", f"{section}.{key}={low}", *base]) != _run(["--set", f"{section}.{key}={high}", *base]):
                return True
    return False


@pytest.mark.os_agnostic
def test_every_documented_key_has_a_probe() -> None:
    """A key nobody wrote a probe for is a key this test silently skips.

    Without this, adding a configuration key and forgetting it here would leave
    the suite green while the new key went unchecked, which is the failure mode
    the whole module exists to close.
    """
    missing = [
        f"{section}.{key}" for section, key in documented_keys() if key not in PROBES and key not in UNOBSERVABLE
    ]
    assert not missing, f"documented keys with no probe value: {missing}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("section", "key"), documented_keys(), ids=str)
def test_a_documented_key_changes_what_lsdsk_does(section: str, key: str, tmp_path: Path) -> None:
    """Set it two ways and the tool must behave two ways.

    Measured before this existed: ``--set display.summary_limit=2`` and ``=1000``
    produced identical bytes, as did the two temperature bands, and
    ``wear_row_floor_percent`` moved only ``lsdsk trend``.
    """
    if key in UNOBSERVABLE:
        pytest.skip(f"{section}.{key}: {UNOBSERVABLE[key]}")
    if key == "wear_projection_min_points":
        store = _rising_wear_store(tmp_path)
    elif key in SEEDED:
        store = _seeded_store(tmp_path)
    else:
        store = None
    assert _moves_something(section, key, store), (
        f"{section}.{key} is documented but changed nothing: both probe values "
        f"{PROBES[key][:2]} gave identical output on every command and every capture. "
        "Either wire it through, or delete it, or add it to UNOBSERVABLE with the reason."
    )


@pytest.mark.os_agnostic
def test_the_probe_can_actually_detect_a_change() -> None:
    """The control. A harness that never sees a difference passes everything.

    Uses a key already proven live, so a failure here means the detector broke
    rather than that a key regressed.
    """
    target = str(FIXTURES / "linux-sas-hba.json")
    same = _run(["--replay", target]) == _run(["--replay", target])
    assert same, "two identical runs differed, so the digest is not stable and every result above is noise"
    differs = _run(["--set", "display.piped_width=60", "--replay", target]) != _run(
        ["--set", "display.piped_width=400", "--replay", target]
    )
    assert differs, "the detector cannot see a change it is pointed straight at"


#: Keys whose effect appears in more than one view, and the commands that must
#: ALL honour them. "Moves something somewhere" is a weaker claim than it reads
#: as: `display.summary_limit` was threaded into the default page and left out
#: of `topology`, and the test above stayed green because bare `lsdsk` answered
#: for both. A key honoured by one of two commands that print the same line is
#: still a broken key.
SHARED_BY: dict[str, tuple[tuple[str, ...], ...]] = {
    "summary_limit": ((), ("topology",)),
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", sorted(SHARED_BY), ids=str)
def test_every_view_that_shows_a_value_honours_its_key(key: str) -> None:
    """One command honouring it is not the same as the key working.

    Both call sites render the same verdict line, so both have to read the same
    setting; the one that does not is invisible to anyone testing the other.
    """
    low, high, _ = PROBES[key]
    target = str(FIXTURES / "linux-sas-hba.json")
    deaf: list[str] = []
    for command in SHARED_BY[key]:
        quiet = _run(["--set", f"display.{key}={low}", *command, "--replay", target])
        loud = _run(["--set", f"display.{key}={high}", *command, "--replay", target])
        if quiet == loud:
            deaf.append(" ".join(command) or "<bare>")
    assert not deaf, f"display.{key} is ignored by: {', '.join(deaf)}"
