"""The Linux reader decodes kernel and udev text as UTF-8 whatever the process locale is.

sysfs, mountinfo and the udev database are written in UTF-8. A read that opens
its file with no ``encoding`` decodes with the locale instead, so under a
Latin-1, ASCII or cp1252 locale a label or a DMI string came back as mojibake
and a character count measured on the wrong text. The suite itself runs in
UTF-8, so these reads are exercised in a child process whose locale is not, and
the child reports which encoding it really ran under so a harness that failed to
change it cannot pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

_CHILD = """
import json, locale, sys
from pathlib import Path
from lsdsk.adapters.hw.linux import mounts
from lsdsk.adapters.hw.linux.reader import read_environment

tree = Path(sys.argv[1])
evidence = read_environment(proc=tree / "proc")
signatures = mounts.read_signatures(["8:2"], tree / "udev")
print(json.dumps({
    "encoding": locale.getencoding(),
    "dmi_vendor": evidence.get("dmi_vendor"),
    "mount_markers": evidence.get("mount_markers"),
    "hypervisor_flag": evidence["hypervisor_flag"],
    "fs_label": signatures["8:2"]["fs_label"],
}))
"""

_E_ACUTE = chr(0xE9)
_VENDOR = f"Caf{_E_ACUTE} {chr(0x4E2D)}{chr(0x6587)} GmbH"
_LABEL = f"r{chr(0xE4)}und{chr(0x2028)}label"


def _lay_out(tree: Path) -> None:
    """A tree holding one non-ASCII value for each read the locale could corrupt."""
    dmi = tree / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "sys_vendor").write_bytes(_VENDOR.encode())
    proc = tree / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "mountinfo").write_bytes(
        f"6226 6210 0:99 / /proc/caf{_E_ACUTE} rw - fuse.lxcfs lxcfs rw\n".encode()
    )
    # Past the prefix read's character bound as ASCII, inside it as UTF-8: only a
    # UTF-8 decode keeps the flag in range.
    (proc / "cpuinfo").write_bytes((_E_ACUTE * 40_000 + " hypervisor\n").encode())
    udev = tree / "udev"
    udev.mkdir()
    (udev / "b8:2").write_bytes(f"E:ID_FS_LABEL={_LABEL}\nE:ID_FS_TYPE=ext4\n".encode())


def _run_child(tree: Path, **environment: str) -> dict[str, Any]:
    """Run the readers in a child under the given environment and return what it saw."""
    env = {**os.environ, **environment}
    done = subprocess.run(  # noqa: S603 - the interpreter running this suite, with a fixed script
        [sys.executable, "-c", _CHILD, str(tree)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        cwd=tree,
    )
    assert done.returncode == 0, done.stderr
    result: dict[str, Any] = json.loads(done.stdout)
    return result


def test_the_readers_decode_utf8_under_a_locale_that_is_not(tmp_path: Path) -> None:
    _lay_out(tmp_path)

    seen = _run_child(tmp_path, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8")

    assert seen["encoding"].lower().replace("-", "") not in {"utf8"}, (
        "the child ran under UTF-8, so this proves nothing"
    )
    assert seen["dmi_vendor"] == _VENDOR
    assert seen["fs_label"] == _LABEL
    assert seen["mount_markers"] == "lxc"
    assert seen["hypervisor_flag"] is True


def test_the_same_tree_reads_the_same_under_utf8(tmp_path: Path) -> None:
    """The control: the expectations above are what a UTF-8 process also reads."""
    _lay_out(tmp_path)

    seen = _run_child(tmp_path, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

    assert seen["encoding"].lower().replace("-", "") == "utf8"
    assert (seen["dmi_vendor"], seen["fs_label"], seen["mount_markers"], seen["hypervisor_flag"]) == (
        _VENDOR,
        _LABEL,
        "lxc",
        True,
    )
