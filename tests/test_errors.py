"""The domain exceptions, tested where they are raised rather than where they are constructed.

Asserting that ``Error("text")`` stringifies to ``"text"`` tests ``Exception``,
which CPython already guarantees, and stays green while nothing in the codebase
raises the type at all. That is how ``UnsupportedPlatformError`` came to
document a branch that raised something else, and how a third exception type
survived being unraisable from anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.hw import snapshot
from lsdsk.domain.errors import ConfigurationError, UnsupportedPlatformError

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from click.testing import CliRunner

SNAPSHOT = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


@pytest.mark.os_agnostic
def test_reading_hardware_on_an_unsupported_platform_names_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch the type documents, driven rather than described.

    ``sys.platform`` is the real external edge here: there is no other way to
    reach the third branch of ``collect`` from a Linux runner, and the reader
    imports below it are genuinely unimportable on the platform being simulated.
    """
    monkeypatch.setattr(snapshot.sys, "platform", "sunos5")

    with pytest.raises(UnsupportedPlatformError) as raised:
        snapshot.collect()

    assert "sunos5" in str(raised.value)
    assert "--replay" in str(raised.value), "the message must name the thing that still works"


@pytest.mark.os_agnostic
def test_an_unsupported_platform_is_still_caught_as_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subclassing is load-bearing, not decorative.

    Every caller that has to catch anything lsdsk raises spells it
    ``ConfigurationError``. Making the platform error a sibling instead would
    let it escape all of them and reach the CLI as an unhandled traceback.
    """
    monkeypatch.setattr(snapshot.sys, "platform", "sunos5")

    with pytest.raises(ConfigurationError):
        snapshot.collect()


@pytest.mark.os_agnostic
def test_the_unsupported_platform_path_exits_with_the_configuration_code(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a caller actually observes: exit 78, and no envelope to mistake for data."""
    monkeypatch.setattr(snapshot.sys, "platform", "sunos5")

    result = cli_runner.invoke(cli, ["topology", "--format", "json"], obj=production_factory)

    assert result.exit_code == 78
    assert "{" not in result.output.split("Error:")[0], "a partial envelope must not precede the error"


@pytest.mark.os_agnostic
def test_replaying_a_capture_still_works_on_an_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """The promise the error message makes, checked rather than trusted.

    Telling a user that ``--replay`` still works while it also refused would be
    worse than saying nothing.
    """
    monkeypatch.setattr(snapshot.sys, "platform", "sunos5")

    assert snapshot.load(SNAPSHOT).hostname


@pytest.mark.os_agnostic
def test_a_configuration_error_carries_what_was_wrong() -> None:
    """The one thing worth asserting about the base type: it is the message a user reads."""
    error = ConfigurationError("snapshot schema 2 is not readable by this version")

    assert "schema 2" in str(error)
