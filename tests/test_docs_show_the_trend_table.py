"""Every document that prints the trend table must name the columns the tool prints.

A rendered example is a copy of an interface, and a copy drifts. When the trend
window's header was renamed, the README was corrected and the shipped skill was
not, so the skill kept printing a header the tool no longer emits - which is
what a reader parsing that table by column would have keyed on.

The expected header comes from ``TREND_COLUMNS`` rather than a list written out
here, so this test cannot disagree with the table it guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lsdsk.adapters.render.trend import TREND_COLUMNS

_ROOT = Path(__file__).resolve().parent.parent
_DOCS = (_ROOT / "README.md", _ROOT / "skills" / "lsdsk" / "SKILL.md")


def printed_header() -> str:
    """The trend table's headers, in the order the renderer sets them."""
    titles = [column.title for column in TREND_COLUMNS]
    assert len(titles) > 4, f"the renderer declared {len(titles)} columns, so this test is not testing"
    return " ".join(titles)


def documented_header(doc: Path) -> str:
    """The header row of the trend example in one document, whitespace normalised."""
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.startswith("device") and "counter" in line and "verdict" in line:
            return " ".join(line.split())
    raise AssertionError(f"{doc.name} carries no trend example, so nothing was checked")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("doc", _DOCS, ids=lambda doc: doc.name)
def test_the_trend_example_names_the_columns_the_tool_prints(doc: Path) -> None:
    """A documented example that names a header the renderer does not print is a lie."""
    assert documented_header(doc) == printed_header()
