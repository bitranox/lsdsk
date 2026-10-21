"""The consent text, the launch-target plan, and the three-way offer choice.

When the offer is accepted lsdsk needs a command for the shortcut to run: it
prefers ``uvx`` (cooldown-protected), falls back to a plainly installed lsdsk,
and otherwise needs uv installed first. When it needs uv, it shows a consent
text carrying the supply-chain rationale and the manual install line for THIS
platform. These pin all three as pure functions, so the orchestration that wires
them is driven by values a test can read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lsdsk.domain.enums import ShortcutChoice
from lsdsk.domain.shortcut import (
    UV_INSTALLER_VERSION,
    consent_text,
    plan_launch,
    uvx_launch_args,
)


@pytest.mark.os_agnostic
def test_the_three_way_choice_has_a_wire_form_for_each_answer() -> None:
    """Create, not now, never - the three answers the offer takes."""
    assert {member.value for member in ShortcutChoice} == {"create", "not-now", "never"}


@pytest.mark.os_agnostic
def test_a_present_uvx_is_the_preferred_launch_target() -> None:
    """With uvx on the machine, the plan is the cooldown-protected uvx command."""
    uvx = Path("/usr/bin/uvx")
    plan = plan_launch(uvx=uvx, installed_lsdsk=Path("/usr/bin/lsdsk"))

    assert plan.needs_uv is False
    # The plan carries the path as this platform renders it: on Windows the same Path reads \usr\bin\uvx.
    assert plan.argv == uvx_launch_args(str(uvx))


@pytest.mark.os_agnostic
def test_a_plain_lsdsk_launcher_is_used_when_uvx_is_absent() -> None:
    """A pip/pipx/uv-tool install already on PATH is the fallback target."""
    lsdsk = Path("/usr/local/bin/lsdsk")
    plan = plan_launch(uvx=None, installed_lsdsk=lsdsk)

    assert plan.needs_uv is False
    assert plan.argv == (str(lsdsk),)


@pytest.mark.os_agnostic
def test_neither_launcher_means_uv_must_be_installed_first() -> None:
    """With no uvx and no lsdsk, the plan is to install uv before anything else."""
    plan = plan_launch(uvx=None, installed_lsdsk=None)

    assert plan.needs_uv is True
    assert plan.argv == ()


@pytest.mark.os_agnostic
def test_the_consent_text_carries_the_rationale_and_this_platforms_manual_line() -> None:
    """POSIX shows the shell installer, Windows the PowerShell one, each pinned."""
    posix = consent_text("linux")
    assert "cooldown" in posix
    assert f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.sh" in posix
    assert "curl" in posix

    windows = consent_text("win32")
    assert f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.ps1" in windows
    assert "irm" in windows
    # A reader on one platform is not shown the other platform's command.
    assert "install.ps1" not in posix
    assert "install.sh" not in windows
