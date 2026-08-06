"""Drive lsdsk against real machines, and assert what only real machines show.

A replayed capture proves the decoders and the pure builders, which is most of
this codebase. It cannot prove the platform transport, because a capture already
contains whatever the transport managed to get: if the reader returns nothing,
the capture faithfully records nothing and every replay test agrees with it.
That blind spot is not hypothetical. Every NVMe identify on Windows was rejected
by the driver for an unknown length of time, and no test could have noticed.

So these run the real CLI on real hosts over ssh, by shipping ``hostprobe.py``
there and reading back one JSON envelope, rather than driving the tool step by
step down the connection.

Marked ``integration`` and ``local_only``: they need this fleet and these
credentials, so CI cannot run them and ``make test`` excludes them. Run with
``make testintegration``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import PROBED, record_probe, record_skip

REPO = Path(__file__).parent.parent.parent
PROBE = Path(__file__).parent / "hostprobe.py"
KEY = Path.home() / ".ssh" / "root@anyhost_nopass.key"

#: Resolved rather than spelled bare, for the same reason the probe resolves its
#: own target: a partial name is looked up against whatever PATH the process
#: happens to have, which is not necessarily the one the author had in mind.
SSH_BIN = shutil.which("ssh")
SCP_BIN = shutil.which("scp")
MAKE_BIN = shutil.which("make")

SSH_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "PreferredAuthentications=publickey",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=10",
]

#: Which machines to probe, read from the environment rather than committed.
#: A fleet's hostnames are somebody's infrastructure, and this repo is public, so
#: they live in `.env` (gitignored, loaded by tests/conftest.py) beside the
#: credentials rather than in the source. `.env.example` carries the format.
#:
#: Format: LSDSK_E2E_HOSTS="host:user:linux,host:user:windows"
HOSTS_VAR = "LSDSK_E2E_HOSTS"


def configured_hosts() -> tuple[tuple[str, str, str], ...]:
    """Parse the host list, or return nothing when none is configured."""
    raw = os.environ.get(HOSTS_VAR, "").strip()
    if not raw:
        return ()
    parsed: list[tuple[str, str, str]] = []
    for entry in raw.split(","):
        parts = [piece.strip() for piece in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            message = f"{HOSTS_VAR} entry {entry!r} is not host:user:linux|windows"
            raise ValueError(message)
        host, user, family = parts
        if family not in {"linux", "windows"}:
            message = f"{HOSTS_VAR} entry {entry!r} has family {family!r}, expected linux or windows"
            raise ValueError(message)
        parsed.append((host, user, family))
    return tuple(parsed)


HOSTS: tuple[tuple[str, str, str], ...] = configured_hosts()


def _ssh(user: str, host: str, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv list, no shell; every element is from this file
        [str(SSH_BIN), "-i", str(KEY), *SSH_OPTIONS, f"{user}@{host}", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )


def _wheel() -> Path:
    """Build the wheel under test, so the fleet runs THIS tree and not PyPI."""
    subprocess.run(  # noqa: S603 - argv list, no shell
        [str(MAKE_BIN), "build"],
        cwd=REPO,
        capture_output=True,
        check=True,
        timeout=900,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
    )
    wheels = sorted((REPO / "dist").glob("lsdsk-*.whl"), key=lambda p: p.stat().st_mtime)
    assert wheels, "make build produced no wheel, so nothing could be shipped"
    return wheels[-1]


@pytest.fixture(scope="module")
def wheel() -> Path:
    """One build, shared by every host in the module."""
    if not (SSH_BIN and SCP_BIN and MAKE_BIN) or not KEY.exists():
        pytest.skip("no ssh/scp/make or no fleet key on this machine")
    return _wheel()


#: Set to keep the shipped files on a host, for debugging a failure there.
KEEP = "LSDSK_E2E_KEEP"


def _uv_pythons(user: str, host: str, family: str) -> str:
    """What uv already manages on this host, so the run only removes its own.

    Asked BEFORE provisioning. Uninstalling unconditionally would take away an
    interpreter somebody else installed, which is somebody else's tooling and
    not this suite's to remove.
    """
    uv = '"C:\\Program Files\\uv\\uv.exe"' if family == "windows" else "uv"
    return _ssh(user, host, f"{uv} python list --only-installed").stdout


def _tidy(user: str, host: str, family: str, wheel_name: str, *, had_python: bool) -> None:
    """Remove what this run put on the host, and nothing else.

    Runs from a ``finally``, so a failed probe still cleans up; and it never
    fails the test, because a leftover file is a smaller problem than a red run
    that hides a green result.
    """
    if os.environ.get(KEEP):
        return
    if family == "windows":
        home = f"C:\\Users\\{user}"
        _ssh(user, host, f'del /q "{home}\\{wheel_name}" "{home}\\hostprobe.py"')
        if not had_python:
            # Fetched by `uv run --python 3.12` for this run alone. Left behind
            # it is a 21 MB interpreter nobody asked for.
            _ssh(user, host, '"C:\\Program Files\\uv\\uv.exe" python uninstall 3.12')
    else:
        _ssh(user, host, f"rm -f /tmp/{wheel_name} /tmp/hostprobe.py")


@pytest.mark.local_only
@pytest.mark.integration
@pytest.mark.parametrize(
    ("host", "user", "family"),
    HOSTS or [pytest.param("", "", "", marks=pytest.mark.skip(reason=f"{HOSTS_VAR} is not set; see .env.example"))],
    ids=[name for name, _, _ in HOSTS] or ["unconfigured"],
)
def test_the_contract_holds_on_real_hardware(wheel: Path, host: str, user: str, family: str) -> None:
    """Ship the probe, run it there, and require every check to pass.

    The probe drives the console script rather than ``python -m lsdsk``: a bare
    interpreter name resolves through the PARENT process's PATH on Windows, and
    under ``uv run --with`` that is an ephemeral build environment which has
    already been removed, so every nested call returns nothing and reads exactly
    like a broken tool.
    """
    if _ssh(user, host, "echo up").returncode != 0:
        record_skip(host, "not reachable from here")
        pytest.skip(f"{host} is not reachable from here")

    had_python = "cpython-3.12" in _uv_pythons(user, host, family)

    if family == "windows":
        home = f"C:\\Users\\{user}"
        destination = f"{user}@{host}:{home}\\"
        command = (
            f'"C:\\Program Files\\uv\\uv.exe" run --no-project --python 3.12 --reinstall '
            f"--with {home}\\{wheel.name} python {home}\\hostprobe.py lsdsk"
        )
    else:
        destination = f"{user}@{host}:/tmp/"
        command = (
            f"cd /tmp && uv run --no-project --python 3.13 --reinstall "
            f"--with /tmp/{wheel.name} python /tmp/hostprobe.py lsdsk"
        )

    copied = subprocess.run(  # noqa: S603 - argv list, no shell
        [str(SCP_BIN), "-i", str(KEY), *SSH_OPTIONS, "-q", str(wheel), str(PROBE), destination],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert copied.returncode == 0, f"could not ship to {host}: {copied.stderr[:200]}"

    try:
        result = _ssh(user, host, command)
    finally:
        _tidy(user, host, family, wheel.name, had_python=had_python)

    start = result.stdout.find("{")
    assert start >= 0, f"{host} returned no envelope. stderr: {result.stderr[-400:]}"
    envelope = json.loads(result.stdout[start:])

    facts = dict(envelope["facts"])
    facts["host"] = envelope["host"]
    facts["checks"] = len(envelope["checks"])
    # Recorded BEFORE the assertions, and printed from pytest_terminal_summary
    # rather than here: a print inside a passing test is swallowed by capture,
    # so the evidence would appear only on the runs that already tell you
    # something is wrong.
    record_probe(facts)

    assert facts.get("disks"), f"{host} reported no disks at all, so this asserted nothing"
    failed = [f"{item['name']}: {item['detail']}" for item in envelope["failed"]]
    assert not failed, f"{host} failed {len(failed)} of {len(envelope['checks'])} checks:\n  " + "\n  ".join(failed)


@pytest.mark.local_only
@pytest.mark.integration
def test_a_windows_host_was_actually_probed() -> None:
    """Windows is where a replayed capture proves the least.

    Its transport shares no line of code with the Linux one, and the only defect
    that ever hid from the whole suite lived there. This asserts the host was
    REACHED, not merely listed: the earlier version checked the configuration,
    which cannot tell a sweep of six machines from a sweep of none, because an
    unreachable host skips and a skipped test renders green.

    Ordered last by name so the host tests have run; if it ever runs first the
    assertion below fails loudly rather than passing on an empty list, which is
    the safe direction for a claim about coverage.
    """
    if not HOSTS:
        pytest.skip(f"{HOSTS_VAR} is not set; see .env.example")
    windows = [facts for facts in PROBED if str(facts.get("platform")) == "Windows"]
    assert windows, (
        "no Windows host was probed in this run. Either none is reachable, or the "
        f"host tests did not run first. Probed: {[f.get('host') for f in PROBED] or 'nothing'}"
    )
    assert windows[0].get("blind_buses") == [], f"a transport read nothing: {windows[0].get('blind_buses')}"
