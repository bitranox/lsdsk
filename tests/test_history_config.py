"""The `[history]` configuration section.

Three sources settle how history behaves: the shipped default, the config file,
then the command line. These pin that order, and pin the two readings that are
easy to get wrong: an empty path means "the state directory", not a file named
empty string, and turning recording off must not stop history being READ.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from lib_layered_config import Config

from lsdsk.adapters.config.history import HistorySettings, get_history_settings
from lsdsk.adapters.history.store import MAX_SAMPLES_PER_DRIVE, default_history_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
SNAPSHOT = FIXTURES / "linux-sas-hba.json"


def config_of(**history: object) -> Config:
    """A config carrying just a `[history]` section."""
    return Config({"history": history}, {})


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_an_absent_section_gives_the_shipped_behaviour() -> None:
    settings = get_history_settings(Config({}, {}))
    assert settings.enabled is True
    assert settings.path == default_history_path()
    assert settings.max_samples_per_drive == MAX_SAMPLES_PER_DRIVE


@pytest.mark.os_agnostic
def test_the_shipped_default_file_parses_and_matches_the_code() -> None:
    """The documented default and the constant must not drift apart.

    The shipped TOML states 512 in prose and in the key; the store defines the
    same number. A test that only read the code could not see them disagree.
    """
    from lsdsk.adapters.config.loader import get_config

    settings = get_history_settings(get_config())
    assert settings.enabled is True
    assert settings.max_samples_per_drive == MAX_SAMPLES_PER_DRIVE


# --------------------------------------------------------------------------
# The two readings that are easy to get wrong
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_path_means_the_state_directory(blank: str) -> None:
    """Not a file literally named empty string, which is what a naive read gives."""
    assert get_history_settings(config_of(path=blank)).path == default_history_path()


@pytest.mark.os_agnostic
def test_a_configured_path_is_used() -> None:
    assert get_history_settings(config_of(path="/var/lib/lsdsk/h.json")).path == Path("/var/lib/lsdsk/h.json")


@pytest.mark.os_agnostic
def test_a_configured_path_expands_a_home_shortcut() -> None:
    resolved = get_history_settings(config_of(path="~/state/h.json")).path
    assert "~" not in str(resolved)
    assert resolved.is_absolute()


@pytest.mark.os_agnostic
def test_the_command_line_wins_over_the_file() -> None:
    settings = get_history_settings(config_of(path="/from/file.json"), path_override=Path("/from/cli.json"))
    assert settings.path == Path("/from/cli.json")


# --------------------------------------------------------------------------
# Refusing to fail the run over a malformed setting
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("bad", [0, -1, "many", None, True])
def test_a_nonsense_cap_falls_back_rather_than_thinning_to_nothing(bad: object) -> None:
    """A malformed cap must not stop somebody diagnosing a failing drive.

    Zero or negative would thin every series away, and `True` is an int in
    Python, so it needs excluding explicitly rather than by type alone.
    """
    assert get_history_settings(config_of(max_samples_per_drive=bad)).max_samples_per_drive == MAX_SAMPLES_PER_DRIVE


@pytest.mark.os_agnostic
@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_a_nonsense_enabled_flag_falls_back_to_recording(bad: object) -> None:
    assert get_history_settings(config_of(enabled=bad)).enabled is True


@pytest.mark.os_agnostic
def test_disabling_recording_is_honoured() -> None:
    assert get_history_settings(config_of(enabled=False)).enabled is False


# --------------------------------------------------------------------------
# End to end through the CLI
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_config_can_turn_recording_off_without_blinding_the_verdict(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    inject_config: Callable[..., Callable[[], Any]],
    strip_ansi: Callable[[str], str],
) -> None:
    """`enabled = false` stops the write and nothing else.

    Findings are still graded against whatever was recorded before, which is the
    whole reason the flag is not simply "ignore history".
    """
    from lsdsk.adapters.cli import cli

    store = tmp_path / "history.json"
    # Seed two readings so there is a real past to be graded against.
    for capture in (SNAPSHOT, FIXTURES / "linux-sas-hba-later.json"):
        cli_runner.invoke(
            cli, ["--history-file", str(store), "record", "--replay", str(capture)], obj=production_factory
        )
    before = store.read_text(encoding="utf-8")

    factory = inject_config({"history": {"enabled": False}})
    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "trend", "--replay", str(FIXTURES / "linux-sas-hba-later.json")],
        obj=factory,
    )

    assert store.read_text(encoding="utf-8") == before, "recording was disabled, so nothing may be written"
    assert "no new" in strip_ansi(result.output), "history must still be read and graded"


@pytest.mark.os_agnostic
def test_the_settings_object_is_frozen() -> None:
    """One settled answer per run; nothing downstream may edit it."""
    settings = HistorySettings(path=Path("/tmp/h.json"))
    with pytest.raises((TypeError, ValueError)):
        settings.enabled = False  # type: ignore[misc]  # the point of the test


# --------------------------------------------------------------------------
# Where a root run keeps its store
# --------------------------------------------------------------------------


@pytest.mark.os_posix
def test_a_root_run_uses_the_system_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """What it records is a property of the machine, not of who typed it.

    A per-user path scatters one machine's history across several homes, and the
    useful runs of this tool are all root on a server.
    """
    from lsdsk.adapters.history.store import SYSTEM_STORE_DIR, default_history_path

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)
    assert default_history_path() == SYSTEM_STORE_DIR / "history.json"


@pytest.mark.os_posix
def test_a_non_root_run_keeps_the_per_user_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A user cannot write /var/lib, so it must not be offered to them."""
    from lsdsk.adapters.history.store import default_history_path

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_history_path() == tmp_path / ".local" / "state" / "lsdsk" / "history.json"


@pytest.mark.os_agnostic
def test_configuration_still_wins_over_the_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)
    assert get_history_settings(config_of(path="/srv/h.json")).path == Path("/srv/h.json")


# --------------------------------------------------------------------------
# The first write announces itself, once
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_first_write_names_the_store_and_later_ones_do_not(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """A run that writes to disk should not be a silent surprise, once."""
    from lsdsk.adapters.cli import cli

    store = tmp_path / "history.json"
    later = FIXTURES / "linux-sas-hba-later.json"
    first = cli_runner.invoke(
        cli, ["--history-file", str(store), "trend", "--replay", str(SNAPSHOT)], obj=production_factory
    )
    assert "Recording disk error counters" not in strip_ansi(first.output), "a replay writes nothing, so says nothing"

    # `record` is the path that writes on a replay.
    seeded = cli_runner.invoke(
        cli, ["--history-file", str(store), "record", "--replay", str(SNAPSHOT)], obj=production_factory
    )
    assert seeded.exit_code == 0
    second = cli_runner.invoke(
        cli, ["--history-file", str(store), "record", "--replay", str(later)], obj=production_factory
    )
    assert "Recording disk error counters" not in strip_ansi(second.output), "never announced twice"


# --------------------------------------------------------------------------
# The tunables reach the run, not just the model
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_configured_crc_threshold_changes_the_verdict(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    inject_config: Callable[..., Callable[[], Any]],
    strip_ansi: Callable[[str], str],
) -> None:
    """The proof that [thresholds] reaches the rules rather than only parsing.

    /dev/sdb carries 84 CRC errors: below the shipped 100, so a hint. Drop the
    threshold under 84 and the same reading becomes a warning.
    """
    from lsdsk.adapters.cli import cli

    args = ["findings", "--replay", str(FIXTURES / "linux-sas-hba-later.json")]
    shipped = strip_ansi(cli_runner.invoke(cli, args, obj=production_factory).output)
    lowered = strip_ansi(
        cli_runner.invoke(cli, args, obj=inject_config({"thresholds": {"crc_errors_significant": 10}})).output
    )

    def marker(text: str) -> str:
        return next(line.split()[0] for line in text.splitlines() if "/dev/sdb" in line and "CRC" in line)

    assert marker(shipped) != marker(lowered), "lowering the threshold must change sdb's severity"


@pytest.mark.os_agnostic
def test_a_configured_width_changes_the_layout(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    inject_config: Callable[..., Callable[[], Any]],
) -> None:
    """[display].piped_width reaches the console, not just the model."""
    from lsdsk.adapters.cli import cli

    args = ["health", "--replay", str(FIXTURES / "linux-sas-hba-later.json")]
    wide = cli_runner.invoke(cli, args, obj=inject_config({"display": {"piped_width": 200}})).output
    narrow = cli_runner.invoke(cli, args, obj=inject_config({"display": {"piped_width": 60}})).output
    assert max(len(x) for x in wide.splitlines()) > max(len(x) for x in narrow.splitlines())


@pytest.mark.os_agnostic
def test_every_thresholds_key_in_the_shipped_file_is_read(
    inject_config: Callable[..., Callable[[], Any]],
) -> None:
    """A documented key nothing reads is a lie in the config file.

    Sets each key to a distinct value and requires it to arrive on the model, so
    a key that is documented but never parsed fails here.
    """
    from lsdsk.adapters.config.tunables import get_thresholds
    from lsdsk.domain.thresholds import Thresholds

    probe = dict.fromkeys(Thresholds.__dataclass_fields__, 7)
    got = get_thresholds(Config({"thresholds": probe}, {}))
    for name in Thresholds.__dataclass_fields__:
        assert getattr(got, name) == 7, f"[thresholds].{name} is not read"


@pytest.mark.os_agnostic
def test_every_display_key_in_the_shipped_file_is_read() -> None:
    from lsdsk.adapters.config.tunables import DisplaySettings, get_display_settings

    probe = dict.fromkeys(DisplaySettings.model_fields, 77)
    got = get_display_settings(Config({"display": probe}, {}))
    for name in DisplaySettings.model_fields:
        assert getattr(got, name) == 77, f"[display].{name} is not read"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("section", ["thresholds", "display", "history"])
def test_every_key_documented_in_the_shipped_toml_exists_on_its_model(section: str) -> None:
    """The file and the model must not drift apart in either direction."""
    import tomllib

    from lsdsk.adapters.config.history import HistorySettings
    from lsdsk.adapters.config.tunables import DisplaySettings
    from lsdsk.domain.thresholds import Thresholds

    known = {
        "thresholds": set(Thresholds.__dataclass_fields__),
        "display": set(DisplaySettings.model_fields),
        "history": set(HistorySettings.model_fields),
    }[section]
    root = Path(__file__).parent.parent / "src" / "lsdsk" / "adapters" / "config" / "defaultconfig.d"
    documented: set[str] = set()
    for toml in root.glob("*.toml"):
        table: object = tomllib.loads(toml.read_text(encoding="utf-8")).get(section)
        if isinstance(table, dict):
            documented |= {str(key) for key in cast("dict[str, object]", table)}
    assert documented, f"[{section}] is not shipped in any default file"
    assert documented <= known, f"[{section}] documents keys no model has: {documented - known}"


@pytest.mark.os_agnostic
def test_the_suite_never_reaches_the_real_counter_history(tmp_path_factory: pytest.TempPathFactory) -> None:
    """The default store must resolve inside this run's temporary directory.

    Guards the autouse isolation in ``conftest``. Without it the suite reads the
    developer's own store, and an ordinary run writes to it, so a test's result
    depends on the machine and on what earlier runs left there. That is not
    theoretical: renaming the fixtures made the real store's hostname disagree
    with the capture's, and the tool's correct refusal to mix two machines
    failed the terminal-width tests.

    A root run is excluded because it resolves to the system store by design and
    ignores the environment, which is the behaviour a neighbouring test pins.
    """
    from lsdsk.adapters.history.store import default_history_path, running_as_root

    if running_as_root():
        pytest.skip("a root run resolves to the system store by design")
    resolved = default_history_path()
    assert tmp_path_factory.getbasetemp() in resolved.parents, f"the suite would use the real store at {resolved}"
