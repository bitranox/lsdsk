"""Run lsdsk's contract against the hardware of the machine this is copied to.

Shipped to a host and run there rather than driven step by step over ssh, so
there is no shell quoting layer between the assertion and the tool, the whole
run survives a dropped connection, and the result comes back as one JSON
envelope instead of text somebody has to parse.

Every check here drives the real CLI against real hardware. Nothing is
substituted: the point is the parts a replayed capture cannot reach, which is
every line of the platform reader - the ioctls on Linux, SetupAPI and
DeviceIoControl on Windows. A capture proves the decoders and the builders; only
a live run proves the transport that fills a capture in the first place.

Usage:
    python hostprobe.py <lsdsk-invocation...>

    python hostprobe.py lsdsk
    python hostprobe.py /opt/venv/bin/lsdsk

Prefer the console script over ``python -m lsdsk``: a bare interpreter name is
the one that goes wrong, and it goes wrong silently.

Prints a JSON envelope: {"ok": bool, "host": str, "checks": [...], "facts": {...}}.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

#: Exit codes the tool documents. Anything else is a contract breach whatever
#: the output said.
DOCUMENTED_EXITS = {0, 1, 2, 13, 22, 70, 74, 78}

#: The commands that report on a machine. Each takes --replay and --format.
REPORTING = ("topology", "controllers", "disks", "health", "smart", "findings", "slots", "trend")

#: Text that means the tool fell over rather than answering.
CRASH_MARKERS = ("Traceback (most recent call last)", "OverflowError", "ZeroDivisionError", "KeyError")


def resolve(invocation: list[str]) -> list[str]:
    """Turn a bare program name into an absolute path before spawning it.

    On Windows CreateProcess searches the PARENT process's PATH, not the child
    environment, and a bare name can resolve to something quite different from
    what the caller meant: under `uv run --with`, an inner `python` resolves to
    an ephemeral build environment that has already been torn down, so every
    nested call returns nothing at all and reads exactly like a broken tool.
    Measured on a Windows host, where lsdsk itself was fine and the probe was
    not.
    """
    if not invocation:
        return invocation
    found = shutil.which(invocation[0])
    return [found, *invocation[1:]] if found else invocation


def run(invocation: list[str], args: list[str]) -> tuple[int, str, str]:
    """One real lsdsk process on this machine."""
    done = subprocess.run(  # noqa: S603 - argv list, no shell; invocation comes from our own argv
        [*resolve(invocation), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return done.returncode, done.stdout, done.stderr


def check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    """One assertion's result, in the shape the runner asserts on."""
    return {"name": name, "passed": bool(passed), "detail": detail}


def exit_codes_and_purity(invocation: list[str]) -> list[dict[str, Any]]:
    """Every reporting command, both modes, against this machine's own hardware."""
    results: list[dict[str, Any]] = []
    for command in REPORTING:
        for mode in ([], ["--format", "json"]):
            code, out, err = run(invocation, [command, *mode])
            label = f"{command}{' json' if mode else ''}"
            results.append(check(f"exit-documented:{label}", code in DOCUMENTED_EXITS, f"exit {code}"))
            crashed = [m for m in CRASH_MARKERS if m in out or m in err]
            results.append(check(f"no-crash:{label}", not crashed, ",".join(crashed)))
            if mode:
                try:
                    json.loads(out)
                    pure = True
                    why = ""
                except Exception as error:
                    pure = False
                    why = f"{type(error).__name__}: {str(error)[:80]}"
                results.append(check(f"stdout-pure-json:{label}", pure, why))
    return results


def round_trip(invocation: list[str], workdir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Capture this machine, replay the capture, and require the same machine back.

    This is the strongest single assertion available on real hardware: it runs
    the platform reader, the capture format and the pure builder end to end, and
    a drift anywhere between them shows up as a different machine.
    """
    capture = workdir / "capture.json"
    code, _, err = run(invocation, ["snapshot", "-o", str(capture)])
    if code != 0 or not capture.exists():
        return [check("round-trip:capture", False, f"exit {code}: {err[:120]}")], {}

    live_code, live_out, _ = run(invocation, ["topology", "--format", "json"])
    replay_code, replay_out, _ = run(invocation, ["topology", "--replay", str(capture), "--format", "json"])
    results = [check("round-trip:capture", True)]
    try:
        live = json.loads(live_out)["data"]
        replayed = json.loads(replay_out)["data"]
    except Exception as error:
        return [*results, check("round-trip:parse", False, f"{type(error).__name__}")], {}

    facts: dict[str, Any] = {
        "hostname": live.get("hostname"),
        "disks": len(live.get("disks") or []),
        "controllers": len(live.get("controllers") or []),
        "privileged": live.get("privileged"),
        "environment": live.get("environment"),
        "capture_bytes": capture.stat().st_size,
        "exit_live": live_code,
        "exit_replay": replay_code,
    }
    for field in ("hostname", "disks", "controllers"):
        got = len(replayed.get(field) or []) if field in {"disks", "controllers"} else replayed.get(field)
        results.append(check(f"round-trip:{field}", got == facts[field], f"live={facts[field]} replay={got}"))
    results.append(
        check("round-trip:has-storage", facts["disks"] > 0, f"{facts['disks']} disks - a host with none proves nothing")
    )
    return results, facts


def _health(disk: dict[str, Any]) -> dict[str, Any]:
    """A disk's health mapping, or an empty one when it reported none."""
    raw = disk.get("health")
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def smart_actually_read(invocation: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Whether the passthrough returned real data, which only a live run shows.

    A replayed capture cannot fail this, because the capture already contains
    whatever the reader managed to get. On Windows the NVMe identify was written
    three bytes past where the driver reads it, so every request was rejected and
    every NVMe drive came back with no model, no serial and no counters - and no
    test that replayed a capture could ever have noticed.
    """
    code, out, _ = run(invocation, ["disks", "--format", "json"])
    if code not in DOCUMENTED_EXITS:
        return [check("smart:ran", False, f"exit {code}")], {}
    try:
        payload: dict[str, Any] = json.loads(out)["data"]
        disks: list[dict[str, Any]] = payload["disks"]
    except Exception as error:
        return [check("smart:parse", False, type(error).__name__)], {}

    privileged = bool(payload.get("privileged"))
    identified = [d for d in disks if d.get("model")]
    with_health = [d for d in disks if _health(d).get("power_on_hours") is not None]
    by_bus: dict[str, int] = {}
    for disk in disks:
        by_bus[str(disk.get("bus"))] = by_bus.get(str(disk.get("bus")), 0) + 1
    facts: dict[str, Any] = {
        "disks": len(disks),
        "identified": len(identified),
        "with_health": len(with_health),
        "by_bus": by_bus,
        "privileged": privileged,
    }
    results = [
        check(
            "smart:every-disk-identified",
            len(identified) == len(disks),
            f"{len(identified)}/{len(disks)} have a model - an empty one means the identify failed",
        )
    ]
    if privileged:
        # PER BUS, not "at least one drive anywhere". The first version of this
        # check asked only whether some drive had counters, and it passed on the
        # broken build: the SATA drive still answered, so 1 > 0 held while the
        # NVMe drive had silently lost everything. A whole transport can fail
        # and the machine still has counters, so a machine-wide count cannot see
        # it. Measured on a Windows host with one drive of each bus - broken 1/2
        # buses, fixed 2/2, and only the per-bus form separates them.
        healthy_buses = {str(d.get("bus")) for d in with_health}
        real_buses = {str(d.get("bus")) for d in disks if str(d.get("bus")) != "virtual"}
        blind = sorted(real_buses - healthy_buses)
        facts["healthy_buses"] = sorted(healthy_buses)
        facts["blind_buses"] = blind
        results.append(
            check(
                "smart:every-bus-reads-counters",
                not blind,
                f"no drive on {blind} reports power-on hours, so that transport read nothing",
            )
        )
    return results, facts


def _speed_text(speed: object) -> str | None:
    """A USB speed as ``<lane rate>x<lanes>``, or None when it was not read."""
    if not isinstance(speed, dict):
        return None
    fields = cast("dict[str, Any]", speed)
    return f"{fields.get('lane_rate')}x{fields.get('lanes')}"


def _usb_of(disk: dict[str, Any]) -> dict[str, Any]:
    """A disk's USB link mapping, or an empty one when it carries none."""
    raw = disk.get("usb")
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def usb_link_read(invocation: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Whether every USB disk on this host came back with the link it runs on.

    Only a live run can fail this: the hub IOCTLs and the usbfs BOS request are
    answered by this machine's own stack, and a capture holds whatever they
    returned. A host with no USB disk passes, and says so in the detail, so a
    green report from it is never read as USB coverage.
    """
    name = "usb:every-usb-disk-has-a-running-link"
    code, out, _ = run(invocation, ["disks", "--format", "json"])
    if code not in DOCUMENTED_EXITS:
        return [check(name, False, f"exit {code}")], {}
    try:
        disks: list[dict[str, Any]] = json.loads(out)["data"]["disks"]
    except Exception as error:
        return [check(name, False, type(error).__name__)], {}

    usb = [disk for disk in disks if disk.get("bus") == "usb"]
    links = {
        str(disk.get("path")): {
            key: _speed_text(_usb_of(disk).get(key)) for key in ("running", "device_max", "port_max")
        }
        for disk in usb
    }
    facts: dict[str, Any] = {"usb_disks": len(usb), "usb_links": links}
    if not usb:
        return [check(name, True, "no usb disk on this host")], facts
    unread = sorted(path for path, link in links.items() if link["running"] is None)
    detail = f"no running link on {unread}" if unread else f"{len(usb)} usb disk(s), every link read"
    return [check(name, not unread, detail)], facts


def _use_text(use: dict[str, Any]) -> str:
    """One recorded use, as ``<kind>:<name>`` plus its mounts when it has any."""
    kind = use.get("kind")
    name = use.get("name") or ""
    mounts = cast("list[str]", use.get("mounts") or [])
    label = f"{kind}:{name}" if name else str(kind)
    return f"{label} -> {', '.join(mounts)}" if mounts else label


def usage_boot_disk_found(invocation: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Whether the boot disk was found, which only a live run's own mounts show.

    A replayed capture cannot fail this, because the capture already holds
    whatever the reader resolved. This is read-only on both platforms, so it
    runs on every host regardless of privilege. Inside a container the host's
    own use of its disks is invisible and every disk reads unread rather than
    finding nothing; that is the other reading this check accepts, and ONLY
    when the envelope says the run was in a container, because on bare metal
    or a guest it means the reading broke. A host with no disk proves nothing
    and fails, and a disk whose JSON carries no ``usage`` key at all is a
    schema regression, not an unread reading.
    """
    name = "usage:boot-disk-found"
    code, out, _ = run(invocation, ["disks", "--format", "json"])
    if code not in DOCUMENTED_EXITS:
        return [check(name, False, f"exit {code}")], {}
    try:
        data: dict[str, Any] = json.loads(out)["data"]
        disks: list[dict[str, Any]] = data["disks"]
    except Exception as error:
        return [check(name, False, type(error).__name__)], {}

    real = [d for d in disks if str(d.get("bus")) != "virtual"]
    keyless = [str(d.get("path")) for d in real if "usage" not in d]
    usages = [d.get("usage") for d in real]
    booted = [u for u in usages if u and u.get("boot")]
    in_container = data.get("environment") == "container"
    facts: dict[str, Any] = {"usage_by_disk": {str(d.get("path")): _usage_fact(d.get("usage")) for d in real}}
    if not real or keyless:
        why = f"no usage key on {', '.join(keyless)}" if keyless else "no disk on this host proves nothing"
        return [check(name, False, why)], facts
    unread_in_container = in_container and all(u is None for u in usages)
    detail = f"{len(booted)} boot disk(s) among {len(usages)}; environment {data.get('environment')}"
    return [check(name, bool(booted) or unread_in_container, detail)], facts


def _usage_fact(usage: Any) -> dict[str, Any] | None:
    """One disk's recorded usage, condensed for the facts the probe reports."""
    if usage is None:
        return None
    uses = cast("list[dict[str, Any]]", usage.get("uses") or [])
    return {"boot": bool(usage.get("boot")), "uses": [_use_text(u) for u in uses]}


def snapshot_refuses_replay(invocation: list[str], workdir: Path) -> list[dict[str, Any]]:
    """It captures the machine it runs on, so --replay must not silently apply.

    It once did, writing THIS machine into a file the caller believed held
    another host's reading, at exit 0.
    """
    source = workdir / "source.json"
    run(invocation, ["snapshot", "-o", str(source)])
    target = workdir / "should-not-exist.json"
    code, _, _ = run(invocation, ["--replay", str(source), "snapshot", "-o", str(target)])
    return [check("snapshot:refuses-global-replay", code == 22, f"exit {code}, expected 22")]


def main() -> int:
    """Run every check and print the envelope."""
    invocation = sys.argv[1:]
    if not invocation:
        print(json.dumps({"ok": False, "error": "no lsdsk invocation given"}))
        return 2

    checks: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"platform": platform.system(), "python": platform.python_version()}
    with tempfile.TemporaryDirectory() as raw:
        workdir = Path(raw)
        checks += exit_codes_and_purity(invocation)
        trip, trip_facts = round_trip(invocation, workdir)
        checks += trip
        facts.update(trip_facts)
        smart, smart_facts = smart_actually_read(invocation)
        checks += smart
        facts.update(smart_facts)
        usb, usb_facts = usb_link_read(invocation)
        checks += usb
        facts.update(usb_facts)
        usage, usage_facts = usage_boot_disk_found(invocation)
        checks += usage
        facts.update(usage_facts)
        checks += snapshot_refuses_replay(invocation, workdir)

    envelope = {
        "ok": all(item["passed"] for item in checks),
        "host": platform.node(),
        "checks": checks,
        "failed": [item for item in checks if not item["passed"]],
        "facts": facts,
    }
    print(json.dumps(envelope, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
