"""``--traceback`` covers a crash anywhere in the root group, not only at logging start.

``apply_traceback_preferences`` runs first in the root group's callback so that a
crash while the configuration loads, while ``--set`` is merged or while the
warnings are computed prints the full traceback the flag asked for. The existing
test in ``test_cli_core.py`` crashes only at the ``init_logging`` port, which
moving the call down to just before ``init_logging`` leaves green. These crash at
the other three sites, each at a real seam: the services container's
``get_config`` port, and a real ``Config`` subclass handed back by it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from lsdsk.adapters import cli as cli_mod
from lsdsk.composition import AppServices, build_production

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class _ConfigThatCrashesWhenRead(Config):
    """A configuration whose ``as_dict`` fails the way a library fault would."""

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        raise OverflowError("reading the configuration failed")


class _ConfigThatCrashesWhenOverridden(Config):
    """A configuration whose ``--set`` merge fails the way a library fault would."""

    def with_overrides(self, overrides: Mapping[str, Any]) -> Config:
        raise OverflowError("merging the override failed")


def _services_whose_config_load_breaks() -> AppServices:
    def _get_config(**_kwargs: object) -> Config:
        raise OverflowError("loading the configuration failed")

    return replace(build_production(), get_config=_get_config)


def _services_handing_back(config: Config) -> Callable[[], AppServices]:
    def _get_config(**_kwargs: object) -> Config:
        return config

    return lambda: replace(build_production(), get_config=_get_config)


_CRASH_SITES: list[tuple[str, Callable[[], AppServices], list[str], str]] = [
    (
        "config-load",
        _services_whose_config_load_breaks,
        [],
        "loading the configuration failed",
    ),
    (
        "report-step",
        _services_handing_back(_ConfigThatCrashesWhenRead({}, {})),
        [],
        "reading the configuration failed",
    ),
    (
        "override-merge",
        _services_handing_back(_ConfigThatCrashesWhenOverridden({}, {})),
        ["--set", "display.wwn_width=30"],
        "merging the override failed",
    ),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("traceback_flag", [True, False])
@pytest.mark.parametrize(("site", "factory", "extra", "message"), _CRASH_SITES, ids=[s[0] for s in _CRASH_SITES])
def test_traceback_covers_a_crash_in_every_step_of_the_root_group(
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
    strip_ansi: Callable[[str], str],
    traceback_flag: bool,
    site: str,
    factory: Callable[[], AppServices],
    extra: list[str],
    message: str,
) -> None:
    """The traceback appears with the flag and is absent without it, at each site."""
    args = [*extra, "disks", "--format", "json"]
    if traceback_flag:
        args = ["--traceback", *args]

    exit_code = cli_mod.main(args, services_factory=factory)

    plain_err = strip_ansi(capsys.readouterr().err)
    assert exit_code != 0, f"{site}: {plain_err}"
    assert message in plain_err, f"{site}: the crash this test injected did not surface: {plain_err}"
    has_traceback = "Traceback (most recent call last)" in plain_err
    assert has_traceback is traceback_flag, f"{site}: {plain_err}"
