"""A refusal whose reason is the empty string is still a refusal.

``str(OSError())`` is ``''`` when the kernel gave an errno and no text, so a
reader that records ``str(error)`` hands ``refusals_of`` an empty reason for a
reading that WAS refused. ``refusals_of`` keyed on the reason being truthy, so it
dropped that reading as though it had not been refused, and ``skipped`` stayed
empty on a run that was incomplete. The rule is "a reason was recorded", which is
``is not None``, never "the reason has text".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from lsdsk.adapters.hw.refusals import refusals_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def test_a_reading_refused_with_an_empty_reason_is_carried() -> None:
    carried = refusals_of({"smart-data": "", "identify": None})

    assert [(one.reading, one.reason) for one in carried] == [("smart-data", "")]


def test_a_reading_that_was_not_refused_is_not_carried() -> None:
    """The control: ``None`` stays the one value that means nothing was refused."""
    assert refusals_of({"smart-data": None, "identify": None}) == ()


def test_a_capture_whose_refusal_text_is_empty_still_reports_the_run_incomplete(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """End to end: the reader's ``''`` reaches ``skipped`` and turns ``ok`` false."""
    from lsdsk.adapters.cli import cli

    data = cast("dict[str, Any]", json.loads((FIXTURES / "linux-sas-hba.json").read_text(encoding="utf-8")))
    node = sorted(data["ata"])[0]
    data["ata"][node].pop("smart_data", None)
    data["ata"][node]["smart_data_error"] = ""
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(data), encoding="utf-8")

    output = cli_runner.invoke(
        cli, ["health", "--replay", str(capture), "--format", "json"], obj=production_factory
    ).output
    envelope = cast("dict[str, Any]", json.JSONDecoder().raw_decode(output[output.index("{") :])[0])

    assert envelope["ok"] is False, envelope["skipped"]
    assert any(entry.startswith("smart-data: ") for entry in envelope["skipped"]), envelope["skipped"]
