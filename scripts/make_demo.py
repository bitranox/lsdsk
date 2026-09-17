"""Record the interactive view as the README's animation, and as its stills.

One storyboard produces both, so the moving picture and the eight stills under
it cannot become two different sittings of the tool.  The app is driven through
Textual's own pilot - the path its tests use - over a committed capture, so the
demo needs no hardware and shows exactly what the tests hold.

**Nothing here names a colour.**  Each frame is whatever the app rendered, so a
palette change lands in the demo without an edit to this file.

**Why not the SVG that ``export_screenshot`` writes.**  It hardcodes
``font-size: 20px`` and precomputes every glyph run's x from that metric, so
editing the font attributes rescales 20px metrics rather than changing the font.
``agg`` lays the character grid out itself from an explicit family and size,
which is the only way to honour a font the pictures are specified in.

Usage::

    python scripts/make_demo.py                  # the GIF and the eight stills
    python scripts/make_demo.py --font-size 14   # a larger grid

``agg`` is not a dependency of this project and is not installed by it: fetch a
release binary from https://github.com/asciinema/agg and put it on PATH, or
point ``--agg`` at it.

System Role:
    Development tooling.  Not imported by the package, and not part of its API.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rich.console import Console  # noqa: E402 - the path above is what makes this importable

from lsdsk.adapters.hw.snapshot import load  # noqa: E402
from lsdsk.adapters.tui.app import LsdskApp  # noqa: E402
from lsdsk.domain.enums import CliCommand  # noqa: E402
from lsdsk.domain.history import History, record  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType

    from lsdsk.domain.models import Disk, Inventory

#: The house size for every picture of this tool. Both numbers are load-bearing:
#: the grid is laid out at render time, so a picture taken at another size is a
#: different picture rather than the same one scaled.
COLS, ROWS = 180, 50

#: 9pt at 96dpi. ``agg`` takes PIXELS, so the point size is converted here once
#: rather than in a reader's head.
DEFAULT_FONT_PX = 12

#: The fontconfig generic family the pictures are specified in. It is an ALIAS,
#: which agg refuses ("no faces matching font family options"), so it is
#: resolved to a real family before agg sees it.
FONT_ALIAS = "Monospace"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

#: Where this script says what it is doing. A Console rather than ``print``,
#: which this tree bans, and it needs no colour of its own.
SAY = Console(highlight=False, markup=False)

#: Which number key opens which page, read from the enum the app builds its own
#: page list from, so reordering the pages cannot leave a step pointing at the
#: wrong one.
KEY_OF = {command.value: str(index + 1) for index, command in enumerate(CliCommand)}


class _Compositor(Protocol):
    """The one private call this needs, named so the call site is typed.

    Textual publishes no way to export the live screen as ANSI;
    ``export_screenshot`` reaches for exactly this and writes SVG instead. The
    shape is declared rather than suppressed, following the repo's other typed
    facades over third-party gaps.
    """

    def render_update(
        self,
        *,
        full: bool = False,
        screen_stack: list[object] | None = None,
        simplify: bool = False,
    ) -> RenderableType | None: ...


@dataclass(frozen=True)
class Step:
    """One captured frame: what to press first, and how long it is held.

    ``must_change`` keeps a dead beat out of the demo. A key the app refuses -
    ``.`` with no clipped WWN under the cursor, ``d`` off the topology page -
    renders the identical screen, and the encoder then merges the two holds, so
    the finished animation looks deliberate while the feature it was meant to
    show never appeared. Measured: the WWN scroll was a no-op for a whole cut,
    because the cursor had been walked off the one drive in this capture whose
    identifier is long enough to be cut.
    """

    keys: tuple[str, ...] = ()
    hold: float = 1.0
    note: str = ""
    settle: int = 1
    must_change: bool = True


#: Which frame each still is taken from, by the name the README references.
STILLS: dict[str, int] = {
    "1-topology.png": 1,
    "2-controllers.png": 7,
    "3-disks.png": 8,
    "4-health.png": 11,
    "5-smart.png": 12,
    "6-findings.png": 13,
    "7-slots.png": 14,
    "8-trend.png": 15,
}


def storyboard() -> tuple[Step, ...]:
    """The demo, in order. Holds are seconds of wall clock in the finished GIF."""
    return (
        Step((KEY_OF["topology"],), 2.6, "open on Topology: the PCI fabric, storage-only", settle=3),
        # The panel is shown BY DEFAULT, so `i` hides it - the opposite of what
        # the key's label suggests. Landing the cursor on a drive is what fills
        # it, and that is the beat worth holding.
        Step(("down",) * 3, 3.0, "the cursor lands on the NVMe; the panel fills with its record"),
        Step(("i",), 1.4, "i hides the panel, giving the fabric the whole page"),
        Step(("i",), 1.6, "i brings it back"),
        Step(("d",), 2.4, "d cycles the tree density: storage and its siblings"),
        Step(("d",), 2.8, "d again: the full fabric"),
        Step(("d",), 1.4, "d wraps back to storage-only"),
        Step((KEY_OF["controllers"],), 2.4, "2 Controllers: ports, free bandwidth, load", settle=2),
        Step((KEY_OF["disks"],), 2.4, "3 Disks: every drive and the link it got", settle=2),
        # Before the cursor walks, not after: the one drive here with an
        # identifier long enough to be cut is the row the cursor opens on, and
        # the key is refused on every other row.
        Step((".",) * 4, 1.8, ". scrolls the strip holding the cut NVMe identifier"),
        Step(("down",) * 4, 1.4, "the cursor walks down the disk table"),
        Step((KEY_OF["health"],), 2.2, "4 Health: wear and error counters", settle=2),
        Step((KEY_OF["smart"],), 2.2, "5 SMART: the raw attributes", settle=2),
        Step((KEY_OF["findings"],), 2.6, "6 Findings: what is worth acting on", settle=2),
        Step((KEY_OF["slots"],), 2.2, "7 Slots: what could be moved where", settle=2),
        Step((KEY_OF["trend"],), 3.0, "8 Trend: counters against the drive's own clock", settle=2),
        Step((KEY_OF["topology"],), 1.6, "back to Topology, closing the loop", settle=2),
    )


def export_ansi(app: LsdskApp) -> str:
    """Export the live screen as ANSI, the way a screenshot is exported as SVG.

    The compositor's full render carries an absolute cursor move per row, so
    each frame repaints the whole screen and no clear is needed between them.
    """
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    compositor = cast("_Compositor", app.screen._compositor)  # pyright: ignore[reportPrivateUsage] - no public ANSI export; remove when Textual grows one
    console.print(compositor.render_update(full=True, simplify=False))
    return console.export_text(styles=True)


def check_frame(text: str, index: int) -> None:
    """Refuse a frame that is not the whole grid, rather than shipping a short one."""
    lines = text.split("\n")
    if len(lines) != ROWS:
        message = f"frame {index}: {len(lines)} rows, expected {ROWS}"
        raise SystemExit(message)
    widths = {len(_ANSI.sub("", line)) for line in lines}
    if widths != {COLS}:
        message = f"frame {index}: visible widths {sorted(widths)}, expected {COLS}"
        raise SystemExit(message)


def history_for(first: Sequence[Disk], later: Sequence[Disk], hostname: str) -> History:
    """Two readings of one host, so the trend page has something to compare.

    Recorded through the real ``record``, so the demo's trend rows are produced
    the way a second run of the tool would produce them.
    """
    started = record(History(hostname=hostname), first, "2026-01-01T00:00:00Z")
    return record(started, later, "2026-02-01T00:00:00Z")


class Cast:
    """An asciicast v2 recording being accumulated.

    A plain class with a typed ``__init__`` rather than a dataclass: pyright
    strict infers ``list[Unknown]`` from a bare ``field(default_factory=list)``,
    ignoring the annotation beside it.
    """

    def __init__(self) -> None:
        """Start an empty recording at time zero."""
        self.events: list[tuple[float, str, str]] = []
        self.clock: float = 0.0

    def add(self, frame: str, hold: float) -> None:
        """Append one frame and advance the clock by its hold."""
        self.events.append((round(self.clock, 3), "o", frame))
        self.clock += hold

    def write(self, path: Path) -> None:
        """Write the recording out as asciicast v2."""
        header = {
            "version": 2,
            "width": COLS,
            "height": ROWS,
            "timestamp": 0,
            "idle_time_limit": 5,
            "title": "lsdsk",
            "env": {"TERM": "xterm-256color"},
        }
        with path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(header) + "\n")
            for moment, kind, data in self.events:
                handle.write(json.dumps([moment, kind, data]) + "\n")


async def record_cast(cast_path: Path) -> list[str]:
    """Drive the app through the storyboard and write the recording.

    Returns:
        The exported frames, in order, so the stills come from the same run.
    """
    fixtures = REPO / "tests" / "fixtures" / "hw"
    machine: Inventory = load(fixtures / "linux-sas-hba.json")
    later: Inventory = load(fixtures / "linux-sas-hba-later.json")

    cast_file = Cast()
    frames: list[str] = []
    dead: list[str] = []
    previous = ""
    app = LsdskApp(machine, history_for(machine.disks, later.disks, machine.hostname))
    async with app.run_test(size=(COLS, ROWS)) as pilot:
        await pilot.pause()
        for index, step in enumerate(storyboard()):
            for key in step.keys:
                await pilot.press(key)
            for _ in range(step.settle):
                await pilot.pause()
            frame = export_ansi(app)
            check_frame(frame, index)
            if step.must_change and frame == previous:
                dead.append(f"{index:2d}  {''.join(step.keys)!r}  {step.note}")
            previous = frame
            frames.append(frame)
            cast_file.add(frame, step.hold)
            SAY.print(f"{index:2d}  {step.hold:4.1f}s  {step.note}")

    if dead:
        message = "steps that pressed a key and changed nothing:\n  " + "\n  ".join(dead)
        raise SystemExit(message)
    cast_file.write(cast_path)
    SAY.print(f"\n{len(frames)} frames, {cast_file.clock:.1f}s -> {cast_path}")
    return frames


def font_family() -> str:
    """What ``Monospace Regular`` resolves to on this machine.

    Asked of fontconfig rather than written down, because the alias is what the
    pictures are specified in and the family behind it differs per machine.
    """
    found = shutil.which("fc-match")
    if found is None:
        raise SystemExit("fc-match is not on PATH, so the font alias cannot be resolved")
    result = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [found, "-f", "%{family[0]}", FONT_ALIAS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def render_gif(agg: str, cast_path: Path, gif: Path, font_px: int) -> None:
    """Lay the recording out on a character grid and write the animation."""
    family = font_family()
    SAY.print(f"==> {family} at {font_px}px, {COLS}x{ROWS}")
    subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [
            agg,
            "--quiet",
            "--font-family",
            family,
            "--font-size",
            str(font_px),
            "--cols",
            str(COLS),
            "--rows",
            str(ROWS),
            "--line-height",
            "1.4",
            "--fps-cap",
            "10",
            "--last-frame-duration",
            "2",
            str(cast_path),
            str(gif),
        ],
        check=True,
    )


def write_stills(gif: Path, into: Path) -> None:
    """Cut the named stills out of the finished animation.

    Taken from the animation rather than rendered again, so a still and the
    moment it comes from are the same picture by construction.
    """
    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        raise SystemExit("ImageMagick is not on PATH, so the stills cannot be cut")
    with_frames = into / "frame-%02d.png"
    subprocess.run([magick, str(gif), "-coalesce", str(with_frames)], check=True)  # noqa: S603 - a fixed argv, no shell
    for name, index in STILLS.items():
        source = into / f"frame-{index:02d}.png"
        source.replace(into / name)
        SAY.print("still", name)
    for leftover in into.glob("frame-*.png"):
        leftover.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    """Build the animation and the stills."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agg", default="agg", help="the asciinema gif generator (default: agg on PATH)")
    parser.add_argument("--font-size", type=int, default=DEFAULT_FONT_PX, help="in PIXELS; 9pt at 96dpi is 12")
    parser.add_argument("--gif", type=Path, default=REPO / "docs" / "media" / "lsdsk-demo.gif")
    parser.add_argument("--stills", type=Path, default=REPO / "docs" / "screenshots")
    parser.add_argument("--cast", type=Path, default=None, help="keep the recording at this path")
    namespace = parser.parse_args(argv)
    # argparse hands back an untyped namespace, so every option is named and
    # typed once here rather than read off it at the point of use, where the
    # type checker can only say "unknown" about each one in turn.
    wanted_agg = cast("str", namespace.agg)
    font_px = cast("int", namespace.font_size)
    gif = cast("Path", namespace.gif)
    stills = cast("Path", namespace.stills)
    keep_cast_at = cast("Path | None", namespace.cast)

    agg = shutil.which(wanted_agg)
    if agg is None:
        SAY.print(f"{wanted_agg} is not on PATH: fetch a release from https://github.com/asciinema/agg", style="red")
        return 2

    gif.parent.mkdir(parents=True, exist_ok=True)
    cast_path = keep_cast_at if keep_cast_at is not None else gif.with_suffix(".cast")
    asyncio.run(record_cast(cast_path))
    render_gif(agg, cast_path, gif, font_px)
    write_stills(gif, stills)
    if keep_cast_at is None:
        cast_path.unlink()
    SAY.print(f"\n{gif}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
