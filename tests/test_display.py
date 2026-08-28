"""Integration tests for config display wrapper.

Tests the thin wrapper that delegates to lib_layered_config's display_config.
The wrapper adds log flushing before display. Core display behavior is tested
in lib_layered_config's test suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from lsdsk.adapters.config.display import display_config
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib_layered_config.domain.config import SourceInfo

# ======================== display_config — error paths ========================


@pytest.mark.os_agnostic
def test_display_config_raises_for_nonexistent_section(
    config_factory: Callable[[dict[str, Any]], Config],
) -> None:
    """Requesting a section that doesn't exist must raise ValueError."""
    config = config_factory({"existing_section": {"key": "value"}})
    with pytest.raises(ValueError, match="not found"):
        display_config(config, output_format=OutputFormat.HUMAN, section="nonexistent")


@pytest.mark.os_agnostic
def test_display_config_raises_for_nonexistent_section_json(
    config_factory: Callable[[dict[str, Any]], Config],
) -> None:
    """Requesting a nonexistent section in JSON format must also raise ValueError."""
    config = config_factory({"existing_section": {"key": "value"}})
    with pytest.raises(ValueError, match="not found"):
        display_config(config, output_format=OutputFormat.JSON, section="nonexistent")


# ======================== display_config — wrapper integration ========================


@pytest.mark.os_agnostic
def test_display_human_renders_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Wrapper must produce human-readable output via lib_layered_config."""
    config = Config({"app_name": "myapp", "section": {"key": "val"}}, {})
    display_config(config, output_format=OutputFormat.HUMAN)
    output = capsys.readouterr().out

    assert 'app_name = "myapp"' in output
    assert "[section]" in output


@pytest.mark.os_agnostic
def test_display_json_renders_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Wrapper must produce JSON output via lib_layered_config."""
    config = Config({"section": {"key": "value"}}, {})
    display_config(config, output_format=OutputFormat.JSON)
    output = capsys.readouterr().out

    assert '"section"' in output
    assert '"key": "value"' in output


@pytest.mark.os_agnostic
def test_display_human_renders_profile_in_provenance(
    capsys: pytest.CaptureFixture[str],
    source_info_factory: Callable[..., SourceInfo],
) -> None:
    """Profile name must pass through to lib_layered_config."""
    metadata: dict[str, SourceInfo] = {
        "section.key": source_info_factory("section.key", "user", "/home/user/.config/app/config.toml"),
    }
    config = Config({"section": {"key": "value"}}, metadata)

    display_config(config, output_format=OutputFormat.HUMAN, profile="production")

    output = capsys.readouterr().out
    assert "# layer:user profile:production" in output


# ======================== Falsey value handling ========================


@pytest.mark.os_agnostic
def test_display_config_displays_section_with_zero_value(capsys: pytest.CaptureFixture[str]) -> None:
    """Section with integer zero value must display (not raise as 'not found')."""
    config = Config({"section": {"count": 0}}, {})

    display_config(config, output_format=OutputFormat.HUMAN, section="section")

    output = capsys.readouterr().out
    assert "count = 0" in output


@pytest.mark.os_agnostic
def test_display_config_displays_section_with_false_value(capsys: pytest.CaptureFixture[str]) -> None:
    """Section with boolean False value must display (not raise as 'not found')."""
    config = Config({"section": {"enabled": False}}, {})

    display_config(config, output_format=OutputFormat.HUMAN, section="section")

    output = capsys.readouterr().out
    # TOML uses lowercase 'false', not Python's 'False'
    assert "enabled = false" in output


@pytest.mark.os_agnostic
def test_display_config_json_displays_section_with_falsey_values(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON format with falsey values must display (not raise as 'not found')."""
    config = Config({"section": {"count": 0, "enabled": False, "items": []}}, {})

    display_config(config, output_format=OutputFormat.JSON, section="section")

    output = capsys.readouterr().out
    assert '"count": 0' in output
    assert '"enabled": false' in output
    assert '"items": []' in output


# --------------------------------------------------------------------------
# The default page is the whole machine, or it is not the default
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_default_page_contains_every_section_a_command_can_show() -> None:
    """CLAUDE.md's central rule, which had no test holding it.

    "A new section is added to ``render_full`` as well as to its own command, or
    the default silently stops being complete." The two tests named as holding
    that check only the pages/commands/enum triangle. Measured: deleting
    ``render_tree``, ``render_smart`` or ``render_findings`` from ``render_full``
    left the whole suite green, so three sections could vanish unnoticed.

    Each section is identified by a line taken from its own renderer rather than
    by a literal typed here, so rewording a heading does not fail this and a
    deleted section cannot pass it.
    """
    import io
    import json
    from pathlib import Path as _Path

    from rich.console import Console

    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render import report, tables
    from lsdsk.adapters.render.full import render_full
    from lsdsk.domain.diagnostics import diagnose

    fixture = _Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
    machine = build_from(json.loads(fixture.read_text(encoding="utf-8")))
    findings = diagnose(machine)
    width = 200

    def rendered(renderable: object) -> str:
        buffer = io.StringIO()
        Console(file=buffer, width=width, no_color=True).print(renderable)
        return buffer.getvalue()

    page = rendered(render_full(machine, findings, width=width))
    sections = {
        "topology tree": report.render_tree(machine, findings, width=width),
        "controllers": tables.render_controllers(machine, findings, width=width),
        "disks": tables.render_disks(machine, findings, width=width),
        "health": tables.render_health(machine, findings, width=width),
        "smart": report.render_smart(machine, width=width),
        "slots": report.render_slots(machine, width=width),
        "findings": report.render_findings(findings),
    }
    missing: list[str] = []
    for name, renderable in sections.items():
        lines = [line.strip() for line in rendered(renderable).splitlines() if len(line.strip()) > 12]
        assert lines, f"the {name} section rendered nothing, so it could not be checked"
        # A majority of its lines, not merely its first. The findings section's
        # opening line is also quoted by the verdict, so a first-line check
        # passed with the whole section deleted.
        present = sum(1 for line in lines if line in page)
        if present * 3 < len(lines) * 2:
            missing.append(f"{name} ({present}/{len(lines)} lines)")
    assert not missing, f"the default page is missing: {', '.join(missing)}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("span_hours", "expected", "must_not_say"),
    [
        pytest.param(0, 0.0, "0.0", id="no hours have passed at all"),
        pytest.param(1, 1 / 42278, "0.0", id="a real rate too small to round"),
    ],
)
def test_a_refusal_never_reports_an_expectation_that_rounded_to_nothing(
    span_hours: int, expected: float, must_not_say: str
) -> None:
    """ "only 0.0 were due" says the opposite of what it means.

    Both readings come from one machine: an NVMe drive on its first recording,
    where no power-on hours have elapsed, and a SATA drive with one lifetime CRC
    error in 42278 hours, whose rate over a one-hour span predicts 0.000024
    errors. Formatted to one decimal both become "0.0", so the sentence reads
    "only none were due" and the word "only" contradicts the number after it.

    The verdict must state why it is refusing in terms that survive rounding.
    """
    from lsdsk.adapters.render.trend import verdict_text
    from lsdsk.domain.history import CounterKind, Trend, TrendVerdict

    trend = Trend(
        kind=CounterKind.CRC_ERRORS,
        verdict=TrendVerdict.TOO_CLOSE,
        latest=1,
        delta=0,
        span_hours=span_hours,
        per_hour=None,
        expected_from_lifetime=expected,
    )
    text = verdict_text(trend)

    assert must_not_say not in text, f"reported an expectation that rounded away: {text!r}"
    assert text.strip(), "a refusal must still say something"


@pytest.mark.os_agnostic
def test_a_zero_hour_refusal_blames_the_counter_and_not_the_first_reading() -> None:
    """A span is measured from where the counter last moved, not from recording.

    So zero hours means the counter moved within this power-on hour, and saying
    it means recording only just began is false wherever it matters most.
    Measured on a real machine: a drive whose store held 43 readings across 531
    power-on hours, and whose CRC count was climbing, was refused with "no
    power-on hours have passed since the first reading".
    """
    from lsdsk.adapters.render.trend import verdict_text
    from lsdsk.domain.history import CounterKind, Trend, TrendVerdict

    trend = Trend(
        kind=CounterKind.CRC_ERRORS,
        verdict=TrendVerdict.TOO_CLOSE,
        latest=101588,
        delta=0,
        span_hours=0,
        per_hour=None,
        expected_from_lifetime=0.0,
    )
    text = verdict_text(trend)

    assert "first reading" not in text, f"a zero span was blamed on when recording began: {text!r}"
    assert text.strip(), "a refusal must still say something"


# --------------------------------------------------------------------------
# The palette has to be READABLE, which is a measurement rather than a taste
# --------------------------------------------------------------------------

#: Backgrounds this tool actually lands on. Two dark, two light: macOS Terminal
#: ships white and Solarized light is common, so a palette tuned only for a dark
#: console is unreadable for half its users.
_BACKGROUNDS: dict[str, tuple[int, int, int]] = {
    "Windows Terminal Campbell": (0x0C, 0x0C, 0x0C),
    "legacy console black": (0x00, 0x00, 0x00),
    "macOS Terminal white": (0xFF, 0xFF, 0xFF),
    "Solarized light": (0xFD, 0xF6, 0xE3),
}

#: WCAG's floor for large text is 3.0 and body text wants 4.5. A saturated hue
#: cannot reach 4.5 on black AND white at once - sweeping the lightness puts the
#: ceiling at 4.2 for a colour serving both - so the gate sits at 4.0.
#:
#: What that buys and what it does not: it rejects UNREADABLE, not suboptimal.
#: Verified by mutation - faint scores 1.7 and fails, Campbell cyan scores 3.0
#: and fails, but the legacy console's #008080 scores 4.1 and PASSES. That
#: colour is legible, just poorer than the palette in use. Raising the gate to
#: 4.2 would pin it to today's exact values and fail on any future adjustment,
#: which is a worse trade than admitting a marginal colour.
_MIN_CONTRAST = 4.0


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(v) for v in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(fore: tuple[int, int, int], back: tuple[int, int, int]) -> float:
    low, high = sorted((_relative_luminance(fore), _relative_luminance(back)))
    return (high + 0.05) / (low + 0.05)


@pytest.mark.os_agnostic
def test_every_palette_colour_is_legible_on_every_background() -> None:
    """No colour may fall below the large-text floor on any real background.

    Measured, because the palette this replaced could not be read: the hint and
    ceiling colour scored 1.7:1, and the warning and opportunity colours fell to
    2.4 and 2.6 on a light background. Two causes compounded - the faint
    attribute, which terminals implement by blending toward the background and
    which therefore removes the contrast it was being asked to provide, and
    naming a colour the terminal resolves through its own palette, where cyan
    ranges from #3A96DD to the legacy console's #008080.

    A colour nobody can read carries no meaning, which makes this the same rule
    as the one at the top of theme.py rather than a separate concern.
    """
    from lsdsk.adapters.render import theme

    palette = {
        name: value
        for name, value in vars(theme).items()
        if name.startswith("STYLE_") and isinstance(value, str) and value.startswith("#")
    }
    assert palette, "the control: no hex colours found, so this asserted nothing"

    failures: list[str] = []
    for name, value in sorted(palette.items()):
        rgb = (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        for where, background in _BACKGROUNDS.items():
            ratio = _contrast(rgb, background)
            if ratio < _MIN_CONTRAST:
                failures.append(f"{name} ({value}) is {ratio:.1f}:1 on {where}")
    assert not failures, "unreadable: " + "; ".join(failures)


@pytest.mark.os_agnostic
def test_nothing_in_the_palette_uses_the_faint_attribute() -> None:
    """``dim`` buys emphasis by removing contrast, which is the one thing a
    diagnostic must not spend.

    Kept separate from the contrast test because it cannot be measured the same
    way: faint is applied by the terminal after the colour is chosen, so a hex
    value can pass the ratio check and still be rendered unreadable by a ``dim``
    sitting in front of it.
    """
    from lsdsk.adapters.render import theme

    offenders = [
        name
        for name, value in vars(theme).items()
        if name.startswith("STYLE_") and isinstance(value, str) and "dim" in value.split()
    ]
    assert not offenders, f"these styles still ask the terminal to reduce contrast: {offenders}"


@pytest.mark.os_agnostic
def test_the_trend_table_names_its_measurement_window_a_span() -> None:
    """The window column must not read as a property of the drive.

    It carries the span the `change` figure covers, measured per counter, so one
    drive legitimately shows a different figure on every row. Headed `over` it
    was read as the drive's total power-on hours, which made three rows for one
    NVMe drive look like three contradictory answers to the same question.
    """
    import io
    import json
    from pathlib import Path as _Path

    from rich.console import Console

    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.trend import render_trend
    from lsdsk.domain.history import DiskSeries, History, Sample, identity_of

    fixture = _Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
    machine = build_from(json.loads(fixture.read_text(encoding="utf-8")))
    tracked = next(disk for disk in machine.disks if identity_of(disk) is not None)
    identity = identity_of(tracked)
    assert identity is not None
    history = History(
        hostname=machine.hostname,
        series=(
            DiskSeries(
                identity=identity,
                model=tracked.model,
                samples=(
                    Sample(power_on_hours=1000, captured_at="a", crc_errors=100),
                    Sample(power_on_hours=1010, captured_at="b", crc_errors=300),
                ),
            ),
        ),
    )

    buffer = io.StringIO()
    Console(file=buffer, width=200, no_color=True).print(render_trend(machine, history, width=200))
    rendered = buffer.getvalue()
    header = next(line for line in rendered.splitlines() if "counter" in line and "verdict" in line)

    assert "span" in header, f"the window column is not named span: {header!r}"
    assert "over" not in header, f"the window column still reads as a drive property: {header!r}"
    assert "10h" in rendered, "the measured window stopped being rendered"
