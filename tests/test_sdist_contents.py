"""The sdist ships what a clean clone ships, wherever it is built.

hatchling drops every .gitignore pattern when the project root itself matches
one, and a git worktree under ``.claude/worktrees/`` has such a root, so a
build there shipped ``.env``, ``handover.md`` and every other ignored file. The
sdist is therefore an allowlist in ``pyproject.toml``. Building an sdist is too
slow for this suite, so these tests hold the configuration that decides it:
the allowlist equals the top-level entries git tracks, and nothing on it names
a state file or a secret.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

# Working files that are gitignored on purpose (see CLAUDE.md) or hold secrets.
# A name on the allowlist is shipped as a FILE whatever `exclude` says, so none
# of these may be on it, and each must be in `exclude` so a copy written inside
# a walked directory is dropped as well.
NEVER_SHIPPED = (
    ".env",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "EXECUTION-USER-REVIEW.md",
    "OPEN-WORK.md",
    "handover.md",
)


def _sdist_config() -> dict[str, list[str]]:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    sdist: dict[str, list[str]] = declared["tool"]["hatch"]["build"]["targets"]["sdist"]
    return sdist


def _git_tracked_top_level() -> set[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed, so the tracked set cannot be read")
    toplevel = subprocess.run(  # noqa: S603 - an absolute git path with fixed arguments
        [git, "rev-parse", "--show-toplevel"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    # An unpacked sdist has no .git of its own and may sit inside some other
    # repository, whose tracked set says nothing about this project.
    if toplevel.returncode != 0 or Path(toplevel.stdout.strip()).resolve() != ROOT:
        pytest.skip("not run from a git checkout of this project")
    listed = subprocess.run(  # noqa: S603 - an absolute git path with fixed arguments
        [git, "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    paths = [entry for entry in listed.stdout.decode("utf-8").split("\0") if entry]
    return {path.split("/", 1)[0] for path in paths}


@pytest.mark.os_agnostic
def test_the_sdist_allowlist_is_exactly_the_top_level_entries_git_tracks() -> None:
    """A clean clone ships every tracked file, so the allowlist must name each top-level entry.

    An entry missing from the list silently drops tracked files from a release;
    an entry that git does not track is something only a working checkout has.
    """
    allowlist = _sdist_config().get("only-include")
    assert allowlist is not None, "the sdist is not an allowlist, so a worktree build ships every ignored file"
    tracked = _git_tracked_top_level()
    assert "src" in tracked, "the control: the tracked set was read"
    assert set(allowlist) == tracked, (
        f"tracked but not shipped: {sorted(tracked - set(allowlist))}; "
        f"shipped but not tracked: {sorted(set(allowlist) - tracked)}"
    )


@pytest.mark.os_agnostic
def test_no_state_file_or_secret_is_on_the_allowlist_and_each_is_excluded_below_it() -> None:
    """The allowlist form alone does not keep a state file out: naming one ships it."""
    config = _sdist_config()
    allowlist = config.get("only-include", [])
    exclude = config.get("exclude", [])
    assert allowlist, "the control: the allowlist was read"
    named = sorted(entry for entry in allowlist if entry in NEVER_SHIPPED or entry.endswith(".prev.md"))
    assert named == [], f"the allowlist ships working files: {named}"
    missing = sorted(set(NEVER_SHIPPED) - set(exclude))
    assert missing == [], f"a copy inside a walked directory would ship: {missing}"
    assert "*.prev.md" in exclude, "a rewritten state file's saved copy would ship"


@pytest.mark.os_agnostic
def test_every_allowlist_entry_exists() -> None:
    """A listed path that is not there is skipped by hatchling without a word."""
    stale = sorted(entry for entry in _sdist_config().get("only-include", []) if not (ROOT / entry).exists())
    assert stale == [], f"listed but absent: {stale}"
