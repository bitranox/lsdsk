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
