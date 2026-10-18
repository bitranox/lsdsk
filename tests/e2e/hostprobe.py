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
from typing import Any, NamedTuple, cast

#: Exit codes the tool documents. Anything else is a contract breach whatever
#: the output said.
DOCUMENTED_EXITS = {0, 1, 2, 13, 22, 70, 74, 78}

#: The commands that report on a machine. Each takes --replay and --format.
REPORTING = ("topology", "controllers", "disks", "health", "smart", "findings", "slots", "trend", "filesystems")

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


class ZpoolRow(NamedTuple):
    """One line of ``zpool list -Hp -o name,size,alloc,free,cap,frag``, in exact bytes.

    ``frag`` is None where zpool prints ``-``: the pool publishes no figure.
    """

    size: int
    alloc: int
    free: int
    cap: int
    frag: int | None


def parse_zpool_list(text: str) -> dict[str, ZpoolRow]:
    """Pools by name from ``zpool list -Hp`` output, refusing a line it cannot read.

    A guessed figure would make the comparison agree with whatever it was fed.
    """
    pools: dict[str, ZpoolRow] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        try:
            if len(fields) != 6:
                raise ValueError(len(fields))
            frag = None if fields[5] == "-" else int(fields[5].rstrip("%"))
            size, alloc, free, cap = (int(value.rstrip("%")) for value in fields[1:5])
            pools[fields[0]] = ZpoolRow(size=size, alloc=alloc, free=free, cap=cap, frag=frag)
        except ValueError as error:
            message = f"zpool list line is not a name and five figures: {line!r}"
            raise ValueError(message) from error
    return pools


def used_percent(row: dict[str, Any]) -> int | None:
    """A pool row's fill as lsdsk's domain states it: ``floor(100 * used / (used + free))``."""
    used, free = row.get("used_bytes"), row.get("free_bytes")
    if used is None or free is None or used + free <= 0:
        return None
    return 100 * used // (used + free)


def _row_frag(row: dict[str, Any] | None) -> int | None:
    """A filesystem row's fragmentation score, None when it carries no ``free_space`` reading."""
    space = (row or {}).get("free_space")
    return None if space is None else space.get("fragmentation_percent")


def _pool_verdict(name: str, row: dict[str, Any] | None, pool: ZpoolRow | None) -> tuple[str | None, dict[str, Any]]:
    """Why one pool's two sides disagree (None when they agree), and the figures both gave."""
    percent = used_percent(row) if row else None
    figures: dict[str, Any] = {
        "pool": name,
        "lsdsk_percent": percent,
        "lsdsk_used_bytes": row.get("used_bytes") if row else None,
        "lsdsk_size_bytes": row.get("size_bytes") if row else None,
        "zpool_cap": pool.cap if pool else None,
        "zpool_alloc": pool.alloc if pool else None,
        "zpool_size": pool.size if pool else None,
        "lsdsk_frag": _row_frag(row),
        "zpool_frag": pool.frag if pool else None,
    }
    if pool is None:
        return f"{name}: lsdsk reports a pool zpool list does not show", figures
    if row is None:
        return f"{name}: zpool list shows it (cap {pool.cap}) and lsdsk reports no row", figures
    # Every disagreement is collected rather than the first returned: a pool
    # wrong on both figures once reported only its percent, so the frag defect
    # surfaced a run later, after the percent one had been fixed.
    reasons: list[str] = []
    # No tolerance: both round DOWN a pool's used / (used + free) in whole numbers
    # (lsdsk's domain floors a pool row as zpool does, where a mount rounds up as
    # df does), so the same bytes give the same figure and any gap is a different
    # figure, not a rounding.
    if percent is None:
        reasons.append(f"lsdsk row has no used/free figures, zpool cap {pool.cap}")
    elif percent != pool.cap:
        reasons.append(f"lsdsk {percent}% vs zpool cap {pool.cap}")
    if figures["lsdsk_frag"] != pool.frag:
        shown = "none" if figures["lsdsk_frag"] is None else f"{figures['lsdsk_frag']}%"
        reasons.append(f"lsdsk frag {shown} vs zpool frag {pool.frag}")
    return (f"{name}: {', '.join(reasons)}" if reasons else None), figures


def judge_pools(rows: list[dict[str, Any]], zpool_text: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every pool either side knows must be known by both, with the same percent.

    ``zpool_text`` is None where there is no ``zpool`` binary. A host with no
    pool on either side makes no claim, which the facts record as zero pools
    checked, so the runner can tell a fleet that checked none from one that did.
    """
    lsdsk_pools = {str(row["name"]): row for row in rows if row.get("kind") == "pool"}
    zpool_pools = parse_zpool_list(zpool_text or "")
    names = sorted(set(lsdsk_pools) | set(zpool_pools))
    verdicts = [_pool_verdict(name, lsdsk_pools.get(name), zpool_pools.get(name)) for name in names]
    facts: dict[str, Any] = {"zfs_pools_checked": len(names), "zfs_pools": [figures for _, figures in verdicts]}
    if not names:
        return [], facts
    broken = [why for why, _ in verdicts if why]
    detail = "; ".join(broken) or f"{len(names)} pool(s) match zpool list exactly"
    return [check("filesystems:pools-agree-with-zpool", not broken, detail)], facts


def _root_figures_unread(root: dict[str, Any]) -> list[str]:
    return [key for key in ("size_bytes", "used_bytes", "free_bytes") if root.get(key) is None]


def root_fstype_from_mounts(text: str) -> str | None:
    """The filesystem type of the LAST mount over ``/`` in ``/proc/self/mounts`` text, or None."""
    found: str | None = None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "/":
            found = fields[2]
    return found


def read_root_fstype() -> str | None:
    """What this Linux host has mounted at ``/``, or None where that cannot be read."""
    try:
        return root_fstype_from_mounts(Path("/proc/self/mounts").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def _zfs_root_covered(rows: list[dict[str, Any]]) -> bool:
    """Whether some pool row carries size, used and free, which is where a ZFS root is accounted."""
    return any(row.get("kind") == "pool" and not _root_figures_unread(row) for row in rows)


def judge_root_row(
    rows: list[dict[str, Any]] | None, environment: object, system: str, root_fstype: str | None
) -> tuple[list[dict[str, Any]], str]:
    """Whether ``/`` is accounted for, and the reason when it is exempt, never silently.

    A row named ``/`` with its figures is the normal answer. lsdsk lists a ZFS
    filesystem under its POOL rather than as a mount, so on a ZFS root the
    accounting is a populated pool row, and that is required instead. Only an
    ABSENT row is otherwise excused, and only inside a container, where the root
    sits on an overlay or a disk lsdsk does not inventory. A row that exists
    with figures missing is a broken reading wherever it is. Windows rows are
    volumes, so ``/`` does not apply there.
    """
    name = "filesystems:root-row-populated"
    if system != "Linux":
        return [], f"not applicable: {system} reports volumes, not a / mount"
    root = next((row for row in rows or [] if row.get("name") == "/"), None)
    if root is None and root_fstype == "zfs":
        covered = _zfs_root_covered(rows or [])
        detail = "zfs root, covered by a populated pool row" if covered else "zfs root and no pool row carries figures"
        return [check(name, covered, detail)], "zfs root: accounted by its pool row" if covered else "missing"
    if root is None:
        if environment == "container":
            return [check(name, True, "")], "exempt: container, / is not on an inventoried disk"
        found = f"no / row among {len(rows or [])} filesystem(s) in a {environment} environment"
        return [check(name, False, found)], "missing"
    unread = _root_figures_unread(root)
    detail = f"/ has no {', '.join(unread)}" if unread else "/ size, used and free read"
    return [check(name, not unread, detail)], "populated" if not unread else "unread"


def _refused_readings(row: dict[str, Any]) -> set[str]:
    """The labels of the readings a row records as refused."""
    refused = cast("list[dict[str, Any]]", row.get("readings_refused") or [])
    return {str(item.get("reading")) for item in refused}


def judge_windows_fragmentation(
    rows: list[dict[str, Any]], skipped: list[str] | tuple[str, ...]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Whether an elevated Windows run tried every NTFS volume's bitmap, and read one.

    The e2e session is elevated, so the run-wide ``needs Administrator`` line
    means the walk was never attempted. Otherwise every NTFS volume must carry a
    reading or a recorded ``volume-bitmap`` refusal: silence is neither. And a
    refusal proves only that the walk was TRIED, so at least one NTFS volume
    must carry a reading: an elevated run whose every bitmap was refused read
    nothing, however correctly it recorded that.

    The facts count the volumes either way, so the coverage file shows whether
    this check had anything to judge rather than only that it was absent.
    """
    name = "filesystems:fragmentation-read"
    ntfs = [row for row in rows if str(row.get("fs_type") or "").upper() == "NTFS"]
    read = [row for row in ntfs if row.get("free_space") is not None]
    refused = [row for row in ntfs if "volume-bitmap" in _refused_readings(row)]
    facts: dict[str, Any] = {
        "ntfs_volumes": len(ntfs),
        "ntfs_volumes_read": len(read),
        "ntfs_volumes_refused": len(refused),
    }
    if not ntfs:
        return [], facts
    needs_admin = [line for line in skipped if line.startswith("fragmentation:") and "needs Administrator" in line]
    if needs_admin:
        return [check(name, False, f"elevated session, yet the run says: {needs_admin[0]}")], facts
    silent = [
        str(row.get("name"))
        for row in ntfs
        if row.get("free_space") is None and "volume-bitmap" not in _refused_readings(row)
    ]
    if silent:
        return [check(name, False, f"no reading and no refusal on {', '.join(silent)}")], facts
    if not read:
        return [check(name, False, f"none of {len(ntfs)} NTFS volume(s) was read: every bitmap was refused")], facts
    return [check(name, True, f"{len(read)} of {len(ntfs)} NTFS volume(s) read, {len(refused)} refused")], facts


def judge_filesystems(
    data: dict[str, Any],
    zpool_text: str | None,
    *,
    system: str,
    root_fstype: str | None,
    skipped: list[str] | tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The filesystems section against this host: the root row, and each ZFS pool against zpool."""
    if "filesystems" not in data:
        return [check("filesystems:section-present", False, "the envelope carries no filesystems key")], {}
    rows: list[dict[str, Any]] | None = data["filesystems"]
    results, root_row = judge_root_row(rows, data.get("environment"), system, root_fstype)
    pool_results, facts = judge_pools(rows or [], zpool_text)
    facts["root_row"] = root_row
    windows: list[dict[str, Any]] = []
    if system == "Windows":
        windows, windows_facts = judge_windows_fragmentation(rows or [], skipped)
        facts.update(windows_facts)
    return [*results, *pool_results, *windows], facts


def read_zpool_list() -> str | None:
    """``zpool list`` as this host prints it, or None when there is no usable ZFS here."""
    zpool = shutil.which("zpool")
    if zpool is None:
        return None
    done = subprocess.run(  # noqa: S603 - argv list, no shell; the binary was resolved just above
        [zpool, "list", "-Hp", "-o", "name,size,alloc,free,cap,frag"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    return done.stdout if done.returncode == 0 else None


class HostView(NamedTuple):
    """What the filesystems judge needs to know about the host, read once.

    A value rather than three reads inside the check, so a test can hand the
    judge a Windows host without being one.
    """

    system: str
    root_fstype: str | None
    zpool_text: str | None


def read_host_view() -> HostView:
    """This host's platform, root filesystem type and ``zpool list`` output."""
    return HostView(system=platform.system(), root_fstype=read_root_fstype(), zpool_text=read_zpool_list())


def filesystems_agree_with_the_host(
    invocation: list[str], *, host: HostView | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drive ``filesystems`` here and judge it against the host's own ``zpool list``.

    Args:
        invocation: How to start lsdsk on this host.
        host: The host's own readings; read here when not given.
    """
    code, out, _ = run(invocation, ["filesystems", "--format", "json"])
    if code not in DOCUMENTED_EXITS:
        return [check("filesystems:ran", False, f"exit {code}")], {}
    seen = read_host_view() if host is None else host
    try:
        envelope = json.loads(out)
        return judge_filesystems(
            envelope["data"],
            seen.zpool_text,
            system=seen.system,
            root_fstype=seen.root_fstype,
            skipped=envelope.get("skipped") or [],
        )
    except Exception as error:
        return [check("filesystems:parse", False, f"{type(error).__name__}: {str(error)[:120]}")], {}


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
        fs_checks, fs_facts = filesystems_agree_with_the_host(invocation)
        checks += fs_checks
        facts.update(fs_facts)
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
