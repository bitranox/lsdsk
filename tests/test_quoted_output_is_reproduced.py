"""Every block of tool output a document quotes is one a live run reproduces.

A quoted sample reads exactly like the tool's own output and is checked by
nobody once it is written, so it drifts in silence: the `lsdsk slots` sample in
FINDINGS.md named its occupants `Samsung 980 PRO 2TB` and `AMD Hawaii XT [Radeon
R9 290X]`, which the occupant column has never printed, while every figure
beside the names was right - which is what made it read as verified.

So each quoted output block is registered in ``QUOTED`` with the command that
produces it and the width it was drawn at, and a live run over a committed
capture has to print every line of it. The registry alone would only hold the
blocks somebody remembered to list, so the second half of this module is the
completeness guard: every fenced block that is SHAPED like output must be in the
registry or in ``ILLUSTRATIVE`` with the reason it is not output at all. A new
sample therefore cannot land unheld.

What counts as output-shaped is decided by the block's info string, because that
is the one thing the author states about a block:

* ``bash``, ``sh``, ``shell``, ``powershell``, ``toml``, ``python`` - what a
  reader TYPES or WRITES into a file. Exempt: none of it claims to be something
  the tool printed.
* anything else, the empty info string included - ``text``, ``json``,
  ``console`` - is output-shaped and must be accounted for. ``json`` is on this
  side deliberately: the skill quotes a JSON envelope the tool writes.

A block is matched by the text its first non-blank line starts with, so it is
found wherever it moves in the document; that text has to pick out exactly one
block, or the entry could be satisfied by the wrong one.

What "reproduces" means, since a quote is usually an excerpt:

* the block's lines are an ORDERED excerpt of one stream of the run - lines may
  be left out, none may be invented, altered or moved;
* a line ending in ``...`` is an elision and stands for a printed line that
  starts with the text before it;
* the banner's version is written ``<version>``, because a page quoting the real
  number would go stale on every release;
* a transcript block, one opening ``$ lsdsk``, quotes its own command, and the
  registered command must begin with exactly that.

The run goes through the console script's own entry point, not the click group,
because that is what renders a usage error the way an installed ``lsdsk`` does.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

import pytest

from lsdsk import __init__conf__
from lsdsk.adapters.cli import main
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path("tests") / "fixtures" / "hw"


def _tracked_pages() -> list[str]:
    """Every Markdown page git tracks at the top of the repository or in ``de/``.

    Asked of git rather than globbed, because the guard is about the documents a
    CLONE has. A glob also reads the gitignored working files a developer keeps
    beside them - ``CLAUDE.md``, a handover - so the guard passed in a clean
    worktree and failed in the checkout it was merged into, naming a file no
    reader of the repository will ever see.

    Returns:
        The pages, as repository-relative POSIX paths.

    Raises:
        RuntimeError: If git is absent or cannot answer. An empty answer would read
            as a repository with no pages, which is the shape that checks nothing.
    """
    git = shutil.which("git")
    if git is None:
        msg = "git is not on PATH, so the tracked pages cannot be listed"
        raise RuntimeError(msg)
    listing = subprocess.run(  # noqa: S603 - argv is built here, no shell
        [git, "-C", str(REPO), "-c", "core.quotePath=false", "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if listing.returncode != 0:
        msg = f"git could not list the tracked files of {REPO}: {listing.stderr.strip()}"
        raise RuntimeError(msg)
    # -z because the default quotes any non-ASCII path, which would drop a page
    # silently rather than report it.
    names = [name for name in listing.stdout.split("\0") if name.endswith(".md")]
    pages = sorted(name for name in names if "/" not in name or (name.startswith("de/") and name.count("/") == 1))
    if not pages:
        msg = f"git listed no pages in {REPO}"
        raise RuntimeError(msg)
    return pages


#: The documents whose fenced blocks are read: every tracked page at the top of
#: the repository and its German twin, and the shipped skill. Listed from git
#: rather than written out, so a new page is guarded from the commit that adds it.
SCANNED = (*_tracked_pages(), "skills/lsdsk/SKILL.md")

#: Info strings that mark a block as input a reader types or writes, never output.
INPUT_LANGUAGES = frozenset({"bash", "sh", "shell", "powershell", "ps1", "toml", "python"})

#: A quoted line ending in this is an ELISION: it stands for a real line that
#: starts with the text before it, the rest of which the document left out.
ELISION = "..."

#: How a document writes the build's version in a quoted banner; see ``normalised``.
VERSION = "<version>"

#: The prompt that opens a transcript block, where the block quotes the command
#: as well as what it printed.
PROMPT = "$ lsdsk "


@dataclass(frozen=True)
class QuotedBlock:
    """One quoted output block, and the run that has to reproduce it.

    Attributes:
        document: The document holding the block, relative to the repository.
        first_line: What the block's first non-blank line starts with.
        argv: The command, after ``lsdsk``. For a transcript block it must
            begin with the command the block itself quotes; a ``--replay`` may
            follow, so the run reads a committed capture and never this machine.
        width: The ``display.piped_width`` the block was drawn at, set ahead of
            ``argv``. ``None`` only for a transcript block, which is run exactly
            as it quotes itself.
        exit_code: The exit code that command leaves, asserted so a sample of a
            command that failed cannot pass on its error text.
        stream: Which stream the block is quoted from. A refusal and a warning
            go to stderr, so that stdout stays what a parser expects.
        history: Captures recorded, in order, into a fresh counter history the
            run reads, for a block that shows a trend.
        profiles: Profile directories created in the user configuration layer
            before the run, for a block that names what is on the machine.
        known_failure: Why the block is known not to reproduce, for a document
            this change may not edit. Runs as a strict xfail, so a fix is noticed.
    """

    document: str
    first_line: str
    argv: tuple[str, ...]
    width: int | None
    exit_code: int
    stream: Literal["stdout", "stderr"] = "stdout"
    history: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()
    known_failure: str | None = None


def _replay(capture: str) -> tuple[str, str]:
    """The ``--replay`` pair for one committed capture."""
    return ("--replay", (FIXTURES / f"{capture}.json").as_posix())


def _twinned(block: QuotedBlock) -> tuple[QuotedBlock, QuotedBlock]:
    """The block, and the same block in the document's German twin.

    The German pages quote the ENGLISH output, because the tool prints one
    language, so the twin is held by the same run rather than left to the
    translation manifest - which asks whether the German prose was re-read, not
    whether the output inside it is still what the tool says.
    """
    return block, replace(block, document=f"de/{block.document}")


#: Two readings of one machine fifteen hours apart: the pair a trend needs.
_SAS_HISTORY = ("linux-sas-hba", "linux-sas-hba-later")

QUOTED: tuple[QuotedBlock, ...] = (
    # One recorded sample, because the page quotes a `Counter trends` section and
    # a machine with no history prints an explanation in its place.
    *_twinned(
        QuotedBlock(
            document="REPORT.md",
            first_line="lsdsk <version>  linux-sas-hba",
            argv=("report", *_replay("linux-sas-hba")),
            width=134,
            exit_code=1,
            history=("linux-sas-hba",),
        )
    ),
    *_twinned(
        QuotedBlock(
            document="FINDINGS.md",
            first_line="device        counter",
            argv=("trend", *_replay("linux-sas-hba-later")),
            width=160,
            exit_code=1,
            history=_SAS_HISTORY,
        )
    ),
    *_twinned(
        QuotedBlock(
            document="FINDINGS.md",
            first_line="Micro-Star",
            argv=("slots", *_replay("linux-nvme-board")),
            width=200,
            exit_code=1,
        )
    ),
    *_twinned(
        QuotedBlock(
            document="CONFIG.md",
            first_line="$ lsdsk --set thresholds.wear_warnning_percent=1 findings",
            argv=("--set", "thresholds.wear_warnning_percent=1", "findings", *_replay("linux-sas-hba")),
            width=None,
            exit_code=2,
            stream="stderr",
        )
    ),
    *_twinned(
        QuotedBlock(
            document="CONFIG.md",
            first_line="$ lsdsk --set thresholds.wear_warning_percent=abc",
            argv=(
                "--set",
                "thresholds.wear_warning_percent=abc",
                "--set",
                "display.tree_density=bogus",
                "findings",
                *_replay("linux-sas-hba"),
            ),
            width=None,
            exit_code=1,
            stream="stderr",
        )
    ),
    *_twinned(
        QuotedBlock(
            document="CONFIG.md",
            first_line="$ lsdsk --set threshold.wear_warning_percent=1 findings",
            argv=("--set", "threshold.wear_warning_percent=1", "findings", *_replay("linux-sas-hba")),
            width=None,
            exit_code=2,
            stream="stderr",
        )
    ),
    *_twinned(
        QuotedBlock(
            document="CONFIG.md",
            first_line="Warning: profile prodd named nothing",
            argv=("--profile", "prodd", "findings", *_replay("linux-minimal")),
            width=120,
            exit_code=0,
            stream="stderr",
            profiles=("prod",),
        )
    ),
    QuotedBlock(
        document="skills/lsdsk/SKILL.md",
        first_line="showing storage and the bridges above it",
        argv=("topology", *_replay("linux-sas-hba")),
        width=120,
        exit_code=1,
        known_failure=(
            "drawn in the ASCII fallback where a UTF-8 console gets box drawing, with a root-port name the PCI "
            "database does not give (it reads 'Xeon E7 v2/Xeon E5 v2/Core i7 PCI Express Root Port 3a'), a name "
            "cut without the '>' clip marker, and a disk table whose model and link columns no width produces "
            "together: 5 of its 8 lines match nothing at any width from 80 to 140"
        ),
    ),
    QuotedBlock(
        document="skills/lsdsk/SKILL.md",
        first_line="kernel-virtual devices, with no controller and no counters",
        argv=("topology", *_replay("linux-minimal")),
        width=120,
        exit_code=0,
        known_failure=(
            "quotes '12 not listed: 8 loop, 3 zd, 1 zram', a tally no committed capture holds; the only capture "
            "carrying kernel-virtual devices prints '3 not listed: 1 loop, 1 zd, 1 zram'"
        ),
    ),
    QuotedBlock(
        document="skills/lsdsk/SKILL.md",
        first_line='{"ok":false,"command":"disks"',
        argv=("disks", "--bogus", "--format", "json"),
        width=120,
        exit_code=2,
    ),
    QuotedBlock(
        document="skills/lsdsk/SKILL.md",
        first_line="device        counter",
        argv=("trend", *_replay("linux-sas-hba-later")),
        width=160,
        exit_code=1,
        history=_SAS_HISTORY,
        known_failure=(
            "every row is real, but /dev/sdj is quoted above /dev/sde and the table sorts by device, so a reader "
            "comparing it against their own run finds the rows in another order"
        ),
    ),
)

#: Output-shaped blocks that are deliberately NOT tool output, each with why.
#: Keyed like the registry, and every key must still pick out a block, so an
#: entry cannot outlive the block it excuses.
ILLUSTRATIVE: dict[tuple[str, str], str] = {
    **{
        (f"{prefix}CONFIG.md", "--set SECTION.KEY=VALUE"): (
            "the grammar of --set, with placeholders a reader substitutes; nothing prints it"
        )
        for prefix in ("", "de/")
    },
    **{
        (f"{prefix}README.md", "# in claude code"): (
            "commands typed into Claude Code to install the skill; input to another program, not lsdsk output"
        )
        for prefix in ("", "de/")
    },
    **{
        (f"{prefix}WHY.md", "port             disk             link"): (
            "a constructed table teaching the three-speed comparison: each row is a case rather than a drive, and "
            "its fourth column is prose the tool never prints"
        )
        for prefix in ("", "de/")
    },
}


@dataclass(frozen=True)
class FencedBlock:
    """One fenced block as a document holds it."""

    document: str
    line: int
    info: str
    body: tuple[str, ...]

    @property
    def first_line(self) -> str:
        """The block's first non-blank line, or empty for an empty block."""
        return next((line for line in self.body if line.strip()), "")

    @property
    def output_shaped(self) -> bool:
        """Whether the block may be something the tool printed, by the rule above."""
        language = self.info.split()[0].lower() if self.info.split() else ""
        return language not in INPUT_LANGUAGES


_FENCE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")


def fenced_blocks(document: str, text: str) -> list[FencedBlock]:
    """Every fenced block in ``text``, closed the CommonMark way.

    A closer is the same fence character, at least as long as the opener, with
    nothing after it; an info string may follow an opener only, and a backtick
    opener's info string may not hold a backtick. A block left open runs to
    the end of the document, as CommonMark renders it.

    Args:
        document: The name the blocks are reported under.
        text: The document's text.

    Returns:
        The blocks, in document order.
    """
    blocks: list[FencedBlock] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        opener = _FENCE.match(lines[index])
        index += 1
        if opener is None or _is_code_span(opener):
            continue
        fence = opener.group("fence")
        start = index
        while index < len(lines) and not _closes(lines[index], fence):
            index += 1
        blocks.append(FencedBlock(document, start, opener.group("info").strip(), tuple(lines[start:index])))
        index += 1
    return blocks


def _is_code_span(opener: re.Match[str]) -> bool:
    """Whether a would-be backtick opener is inline code, by holding a backtick in its info string."""
    return opener.group("fence").startswith("`") and "`" in opener.group("info")


def _closes(line: str, fence: str) -> bool:
    """Whether ``line`` closes a block opened by ``fence``."""
    stripped = line.strip()
    return stripped.startswith(fence[0] * len(fence)) and set(stripped) == {fence[0]}


def _blocks_of(document: str) -> list[FencedBlock]:
    return fenced_blocks(document, (REPO / document).read_text(encoding="utf-8"))


def _the_block(document: str, first_line: str) -> FencedBlock:
    """The one block in ``document`` whose first line starts with ``first_line``."""
    found = [block for block in _blocks_of(document) if block.first_line.startswith(first_line)]
    assert len(found) == 1, f"{document}: {len(found)} blocks start with {first_line!r}; the key must pick out one"
    return found[0]


# --------------------------------------------------------------------------
# The reproduction
# --------------------------------------------------------------------------


def _significant(lines: Iterable[str]) -> list[str]:
    return [line.rstrip() for line in lines if line.strip()]


def normalised(output: str) -> str:
    """The run's output with the build's own version written as ``<version>``.

    The banner names the build that produced the page, so a document quoting
    the real number would go stale on every release and the release would red
    this test for a change nobody made to the page. Only the banner's spelling
    is replaced, so a firmware or driver version that happens to read the same
    is left alone.

    Args:
        output: What the run printed.

    Returns:
        The same text, with ``lsdsk 1.4.0`` (whatever this build is) written as
        ``lsdsk <version>``.
    """
    return output.replace(f"lsdsk {__init__conf__.version}  ", f"lsdsk {VERSION}  ")


def _matches(quoted: str, produced: str) -> bool:
    """Whether one quoted line stands for one printed line."""
    if quoted.endswith(ELISION):
        return produced.startswith(quoted.removesuffix(ELISION).rstrip())
    return quoted == produced


def unreproduced(quoted: list[str], produced: list[str]) -> list[str]:
    """The quoted lines a run's output does not contain, in the order quoted.

    The quote must be an ordered EXCERPT of the output: lines may be left out,
    but none may be invented, reordered or altered. Each quoted line is matched
    against the printed lines after the one its predecessor matched.

    Args:
        quoted: The block's non-blank lines.
        produced: The run's non-blank lines.

    Returns:
        Every quoted line with no match at or after its place, empty when the
        whole block reproduces.

    Examples:
        >>> unreproduced(["a", "c"], ["a", "b", "c"])
        []
        >>> unreproduced(["c", "a"], ["a", "b", "c"])
        ['a']
        >>> unreproduced(["b ..."], ["a", "b and more"])
        []
    """
    missing: list[str] = []
    position = 0
    for line in quoted:
        hit = next((at for at in range(position, len(produced)) if _matches(line, produced[at])), None)
        if hit is None:
            missing.append(line)
            continue
        position = hit + 1
    return missing


class Run(NamedTuple):
    """What one run of the entry point left behind."""

    code: int
    stdout: str
    stderr: str


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> Run:
    """Run the console script's own entry point in-process, and take what it wrote.

    The entry point rather than the click group, because that is what a reader
    ran: it is where a usage error is rendered, and a ``CliRunner`` driving the
    group directly draws the same refusal as a boxed panel naming ``cli`` where
    the installed ``lsdsk`` prints one plain ``Error:`` line.
    """
    capsys.readouterr()
    code = main(argv, services_factory=build_production)
    taken = capsys.readouterr()
    return Run(code, normalised(taken.out), normalised(taken.err))


def _seed_history(store: Path, captures: tuple[str, ...], capsys: pytest.CaptureFixture[str]) -> None:
    for capture in captures:
        seeded = _invoke(["--history-file", str(store), "record", *_replay(capture)], capsys)
        assert seeded.code == 0, f"recording {capture} into the history failed: {seeded.stderr}"
    assert store.is_file(), "the control: no history was written, so the trend below has no past to read"


def _seed_profiles(root: Path, profiles: tuple[str, ...]) -> None:
    for profile in profiles:
        directory = root / "profile" / profile
        directory.mkdir(parents=True)
        (directory / "config.toml").write_text("[display]\nwwn_width = 24\n", encoding="utf-8")
    get_config.cache_clear()


def run_block(block: QuotedBlock, *, state: Path, config_root: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    """Run the command a registered block names, with the state it needs.

    Args:
        block: The registered block.
        state: A directory of the test's own for a counter history.
        config_root: The user configuration directory this platform reads.
        capsys: Where the run's two streams are taken from.

    Returns:
        The finished run.
    """
    argv: list[str] = []
    if block.history:
        store = state / "history.json"
        _seed_history(store, block.history, capsys)
        argv += ["--history-file", str(store)]
    _seed_profiles(config_root, block.profiles)
    if block.width is not None:
        argv += ["--set", f"display.piped_width={block.width}"]
    return _invoke([*argv, *block.argv], capsys)


def _quoted_lines(block: QuotedBlock, fenced: FencedBlock) -> list[str]:
    """The lines a run must reproduce, with a transcript's own command set aside."""
    lines = _significant(fenced.body)
    if not lines[0].startswith(PROMPT):
        assert block.width is not None, f"{block.document}: only a transcript block may leave its width unset"
        return lines
    quoted_argv = tuple(shlex.split(lines[0].removeprefix(PROMPT)))
    assert block.argv[: len(quoted_argv)] == quoted_argv, (
        f"{block.document}: the block quotes `lsdsk {shlex.join(quoted_argv)}` and the registry runs "
        f"`lsdsk {shlex.join(block.argv)}`"
    )
    assert block.width is None, f"{block.document}: a transcript runs as it quotes itself, so it takes no width"
    return lines[1:]


def _param(block: QuotedBlock) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=block.known_failure)] if block.known_failure else []
    return pytest.param(block, id=f"{block.document}:{block.first_line[:32]}", marks=marks)


@pytest.fixture
def config_root(user_config_dir: Path) -> Iterator[Path]:
    """The user configuration directory, with the loader's cache cleared on both sides."""
    get_config.cache_clear()
    yield user_config_dir
    get_config.cache_clear()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("block", [_param(block) for block in QUOTED])
def test_a_quoted_block_is_output_the_tool_really_produces(
    block: QuotedBlock,
    tmp_path: Path,
    config_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every line a document quotes is one the registered run prints, in order, on its stream.

    The stream is part of the claim: a refusal and a warning are written to
    stderr so that ``--format json`` on stdout stays parseable, and a document
    quoting one is telling a reader where to look for it.
    """
    fenced = _the_block(block.document, block.first_line)
    quoted = _quoted_lines(block, fenced)
    assert quoted, f"the control: {block.document}'s block quotes nothing, so this asserted nothing"

    run = run_block(block, state=tmp_path, config_root=config_root, capsys=capsys)
    assert run.code == block.exit_code, f"`lsdsk {shlex.join(block.argv)}` left {run.code}, not {block.exit_code}"
    produced = _significant((run.stdout if block.stream == "stdout" else run.stderr).splitlines())
    assert produced, f"the control: the command wrote nothing to {block.stream}, so this asserted nothing"

    missing = unreproduced(quoted, produced)
    assert not missing, f"{block.document} quotes lines {block.stream} does not carry, in that order: {missing}"


# --------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------


def unaccounted(blocks: list[FencedBlock]) -> list[str]:
    """Every output-shaped block that is neither registered nor excused.

    Args:
        blocks: Fenced blocks from any number of documents.

    Returns:
        ``document:line  first line`` for each block nothing accounts for.
    """
    keys = [(block.document, block.first_line) for block in QUOTED] + list(ILLUSTRATIVE)
    return [
        f"{block.document}:{block.line}  {block.first_line}"
        for block in blocks
        if block.output_shaped
        and not any(document == block.document and block.first_line.startswith(first) for document, first in keys)
    ]


def test_the_scan_reads_the_documents_it_claims_to() -> None:
    """The control for the guard below: an empty scan would pass it trivially."""
    scanned = set(SCANNED)
    for required in ("REPORT.md", "de/REPORT.md", "FINDINGS.md", "CONFIG.md", "skills/lsdsk/SKILL.md"):
        assert required in scanned, f"{required} is not scanned, so a block in it could land unheld"
    shaped = [block for document in SCANNED for block in _blocks_of(document) if block.output_shaped]
    assert len(shaped) >= len(QUOTED), f"the scan found {len(shaped)} output-shaped blocks, fewer than registered"


def test_every_output_shaped_block_is_registered_or_excused() -> None:
    """A new quoted sample fails here until it is held or said not to be output."""
    blocks = [block for document in SCANNED for block in _blocks_of(document)]
    missing = unaccounted(blocks)
    assert not missing, (
        "quoted output held by nothing - register it in QUOTED with the command that prints it, "
        f"or in ILLUSTRATIVE with why it is not output: {missing}"
    )


def test_an_unregistered_block_is_reported() -> None:
    """The negative control: the guard above must be able to fail."""
    planted = fenced_blocks("REPORT.md", "prose\n\n```\nDisks on linux-sas-hba   invented\n```\n")
    assert unaccounted(planted) == ["REPORT.md:3  Disks on linux-sas-hba   invented"]
    typed = fenced_blocks("REPORT.md", "```bash\nlsdsk disks\n```\n")
    assert unaccounted(typed) == [], "a bash block is input, so the rule must not claim it"


@pytest.mark.parametrize(("document", "first_line"), list(ILLUSTRATIVE))
def test_every_excuse_still_names_a_block(document: str, first_line: str) -> None:
    """An excuse whose block is gone would silently excuse the next one there."""
    assert _the_block(document, first_line).output_shaped, f"{document}: the excused block is not output-shaped"
