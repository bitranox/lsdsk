"""The sdist ships what a clean clone ships, wherever it is built.

hatchling drops every .gitignore pattern when the project root itself matches
one, and a git worktree under ``.claude/worktrees/`` has such a root, so a
build there shipped ``.env``, ``handover.md`` and every other ignored file. The
sdist is therefore an allowlist in ``pyproject.toml``. Building an sdist is too
slow for this suite, so these tests hold what decides it: the allowlist equals
the top-level entries git tracks, nothing on it names a state file or a secret,
and the file walk hatchling itself runs for an sdist, asked directly from
``SdistBuilder`` rather than imitated with git, yields exactly the files git
tracks - from whichever checkout the suite runs in, a ``.claude/worktrees/`` one
included.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePath

import pytest
from hatchling.builders.sdist import SdistBuilder

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


def _git() -> str:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed, so the tracked set cannot be read")
    return git


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


def _without_git_variables() -> dict[str, str]:
    """This environment minus every ``GIT_`` variable, so a hook's ``GIT_DIR`` cannot redirect a question."""
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def _git_ls_files(root: Path) -> set[str]:
    """Every file git tracks in the repository at ``root``, as forward-slash paths.

    Args:
        root: The repository's top level.

    Returns:
        The tracked paths, relative to ``root``.
    """
    listed = subprocess.run(  # noqa: S603 - an absolute git path with fixed arguments
        [_git(), "ls-files", "-z"],
        cwd=root,
        env=_without_git_variables(),
        capture_output=True,
        check=True,
    )
    return {entry for entry in listed.stdout.decode("utf-8", errors="replace").split("\0") if entry}


def _hatchling_would_ship(root: Path) -> set[str]:
    """Every file hatchling's own walk puts into an sdist built from ``root``.

    This is the walk ``python -m build --sdist`` runs, asked directly rather than
    imitated: whether the ``.gitignore`` patterns apply at all depends on the path
    of the project root (hatchling drops every one when the root matches one, as a
    ``.claude/worktrees/`` checkout does), and a pattern is matched file by file,
    so a ``!`` re-inclusion reaches into a directory git never descends. The
    generated ``PKG-INFO`` is not a file of the tree and is left out.

    Args:
        root: The project root, holding the ``pyproject.toml`` to build from.

    Returns:
        The distribution paths, relative to the sdist's top folder.
    """
    builder = SdistBuilder(str(root))
    shipped = {PurePath(included.distribution_path).as_posix() for included in builder.recurse_included_files()}
    return shipped - {"PKG-INFO"}


def _shipped_but_untracked(root: Path) -> list[str]:
    """Files an sdist built from ``root`` would carry that a clean clone does not have.

    Args:
        root: A git repository whose top level is a hatchling project.

    Returns:
        The offending paths, sorted.
    """
    return sorted(_hatchling_would_ship(root) - _git_ls_files(root))


def test_a_local_sdist_ships_exactly_the_files_git_tracks() -> None:
    """The walk hatchling runs over this checkout ships what a clean clone ships, file for file.

    ``only-include`` names whole directories such as ``docs``, and what hatchling
    then drops below them depends on where the checkout sits: from the main
    checkout it applies the root ``.gitignore`` (and neither ``.git/info/exclude``
    nor a nested one), from a ``.claude/worktrees/`` checkout it applies no
    pattern at all. A file named here would reach a release built from this tree.
    Keep it out of the listed directories, or out of the sdist with an ``exclude``
    entry.
    """
    _git_tracked_top_level()  # skips outside a git checkout of this project
    shipped = _hatchling_would_ship(ROOT)
    tracked = _git_ls_files(ROOT)
    assert "pyproject.toml" in shipped, "the control: the walk was read"

    assert sorted(shipped - tracked) == [], (
        f"a local sdist would ship these untracked files: {sorted(shipped - tracked)}"
    )
    assert sorted(tracked - shipped) == [], f"a local sdist would drop these tracked files: {sorted(tracked - shipped)}"


def _project_repository(root: Path, gitignore: str) -> Path:
    """A git repository at ``root`` holding a minimal hatchling project that ships ``docs``.

    Its ``pyproject.toml``, ``.gitignore`` and ``docs/kept.txt`` are tracked;
    nothing is committed, because ``git ls-files`` reads the index.

    Args:
        root: Where to create the repository.
        gitignore: The text of its root ``.gitignore``.

    Returns:
        ``root``.
    """
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0"\n\n'
        "[tool.hatch.build.targets.sdist]\n"
        'only-include = [".gitignore", "docs", "pyproject.toml"]\n',
        encoding="utf-8",
        newline="\n",
    )
    (root / ".gitignore").write_text(gitignore, encoding="utf-8", newline="\n")
    (root / "docs").mkdir()
    (root / "docs" / "kept.txt").write_text("tracked\n", encoding="utf-8", newline="\n")
    env = _without_git_variables()
    subprocess.run([_git(), "init", "-q", str(root)], env=env, check=True)  # noqa: S603 - an absolute git path with fixed arguments
    subprocess.run(  # noqa: S603 - an absolute git path with fixed arguments
        [_git(), "add", "pyproject.toml", ".gitignore", "docs/kept.txt"], cwd=root, env=env, check=True
    )
    return root


def test_the_check_names_an_ignored_file_a_worktree_build_ships(tmp_path: Path) -> None:
    """Under a ``.claude/worktrees/`` path the root matches ``.claude/``, so hatchling applies no pattern."""
    root = _project_repository(tmp_path / ".claude" / "worktrees" / "x", ".claude/\ndocs/local/\n")
    (root / "docs" / "local").mkdir()
    (root / "docs" / "local" / "a.txt").write_text("planted\n", encoding="utf-8", newline="\n")

    assert _shipped_but_untracked(root) == ["docs/local/a.txt"]


def test_the_check_names_a_file_reincluded_below_an_ignored_directory(tmp_path: Path) -> None:
    """Git never descends into ``docs/local/``, but hatchling matches ``!.env.example`` file by file."""
    root = _project_repository(tmp_path / "checkout", "docs/local/\n.env.*\n!.env.example\n")
    (root / "docs" / "local").mkdir()
    for name in (".env.example", ".env.secret"):
        (root / "docs" / "local" / name).write_text("planted\n", encoding="utf-8", newline="\n")

    assert _shipped_but_untracked(root) == ["docs/local/.env.example"]


def test_the_check_passes_a_tree_whose_ignored_files_hatchling_drops(tmp_path: Path) -> None:
    """The control: outside a worktree path the root ``.gitignore`` applies, so an ignored file is not reported."""
    root = _project_repository(tmp_path / "checkout", "docs/local/\n")
    (root / "docs" / "local").mkdir()
    (root / "docs" / "local" / "a.txt").write_text("planted\n", encoding="utf-8", newline="\n")

    assert _hatchling_would_ship(root) == {".gitignore", "docs/kept.txt", "pyproject.toml"}
    assert _shipped_but_untracked(root) == []
