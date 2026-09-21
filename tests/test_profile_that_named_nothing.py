"""A `--profile` that matches nothing must say so rather than report defaults.

A profile REPLACES the configuration directories rather than adding to them, so
a name with one letter wrong reads no file at all and every value silently falls
back to the shipped one. Nothing refuses it and nothing warns: the run exits 0
and `lsdsk config` afterwards confirms the wrong figures as though they were in
force. A scheduled `lsdsk --profile production findings` would report on the
default thresholds forever.

Every other identifier a reader types at this CLI already answers back - a
misspelled `--set KEY` is refused outright, an unknown file key gets a
did-you-mean, an invalid profile SYNTAX exits 22 - so this was the one
well-formed name that was allowed to mean nothing quietly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.profiles import contributed_layers, existing_profiles, nearest_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from click.testing import CliRunner

#: The setting the fixtures move, chosen because it is an integer a reader can
#: see in `lsdsk config` and is not judged against anything.
SETTING = "display.wwn_width"
PROFILE_VALUE = 9


@pytest.fixture
def config_root(user_config_dir: Path) -> Iterator[Path]:
    """A configuration home holding one real profile called ``prod``.

    Built where THIS platform's loader looks rather than under ``XDG_CONFIG_HOME``,
    which only Linux reads. Seeded the old way these tests passed here and failed
    on every macOS runner, reading the shipped 24 instead of the fixture's 9 -
    a failure about the seeded directory, not about profiles.
    """
    profile_dir = user_config_dir / "profile" / "prod"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.toml").write_text(f"[display]\nwwn_width = {PROFILE_VALUE}\n", encoding="utf-8")
    get_config.cache_clear()
    yield user_config_dir
    get_config.cache_clear()


@pytest.mark.os_posix
def test_the_real_profile_is_what_the_fixture_says_it_is(config_root: Path) -> None:
    """The control every other test here rests on.

    Without it a broken fixture would make the misspelling tests pass by making
    every profile miss, which is the answer they are looking for.
    """
    assert config_root.exists()
    assert get_config(profile="prod").get(SETTING) == PROFILE_VALUE
    assert get_config(profile="prodd").get(SETTING) != PROFILE_VALUE


@pytest.mark.os_posix
@pytest.mark.parametrize("typed", ["prodd", "PROD"])
def test_a_profile_nothing_answered_to_is_reported(
    typed: str, config_root: Path, cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """A near miss and a wrong case, which both read as a working run today."""
    del config_root
    result = cli_runner.invoke(cli, ["--profile", typed, "config"], obj=production_factory)
    warned = " ".join((result.stderr or "").split())

    assert f"profile {typed}" in warned, warned or "(nothing on stderr)"
    assert "prod" in warned, f"the profile that does exist is not suggested: {warned}"


@pytest.mark.os_posix
def test_a_profile_that_did_contribute_is_not_warned_about(
    config_root: Path, cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """The other direction, so the warning cannot simply always fire."""
    del config_root
    result = cli_runner.invoke(cli, ["--profile", "prod", "config"], obj=production_factory)
    assert "profile" not in (result.stderr or "").casefold().replace("profile:", ""), result.stderr


@pytest.mark.os_posix
def test_no_profile_at_all_is_not_a_profile_that_named_nothing(
    config_root: Path, cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """The commonest run of all must stay silent."""
    del config_root
    result = cli_runner.invoke(cli, ["config"], obj=production_factory)
    assert "named nothing" not in (result.stderr or ""), result.stderr


@pytest.mark.os_posix
def test_the_layers_a_profile_contributed_are_read_from_provenance(config_root: Path) -> None:
    """Detection keys on what LOADED, not on a directory being present.

    A directory that exists but holds nothing readable is a silent no-op in
    exactly the same way, so the question is which layers a run actually got.
    """
    del config_root
    assert contributed_layers(get_config(profile="prod"), "prod")
    assert not contributed_layers(get_config(profile="prodd"), "prodd")


@pytest.mark.os_posix
def test_an_empty_profile_directory_counts_as_naming_nothing(user_config_dir: Path) -> None:
    """The case a directory check would call a success.

    It is listed as an existing profile, because a reader who made the
    directory meant that name - but nothing loaded from it, so the run is
    reporting defaults and must say so.
    """
    (user_config_dir / "profile" / "hollow").mkdir(parents=True)
    get_config.cache_clear()
    try:
        assert "hollow" in existing_profiles()
        assert not contributed_layers(get_config(profile="hollow"), "hollow")
    finally:
        get_config.cache_clear()


@pytest.mark.os_posix
def test_the_profiles_on_this_machine_are_the_directories_that_exist(config_root: Path) -> None:
    """What a suggestion is drawn from."""
    del config_root
    assert existing_profiles() == ("prod",)


@pytest.mark.os_agnostic
def test_a_suggestion_is_offered_only_when_one_is_close() -> None:
    """Naming a wildly different profile would be a guess dressed as help."""
    assert nearest_profile("prodd", ("prod", "staging")) == "prod"
    assert nearest_profile("PROD", ("prod", "staging")) == "prod"
    assert nearest_profile("something-else-entirely", ("prod", "staging")) is None
    assert nearest_profile("prod", ()) is None
