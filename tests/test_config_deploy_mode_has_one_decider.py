"""A permission mode is judged once, by the deploy that applies it, and a ``--set`` mode reaches it.

lsdsk used to judge a configured mode with a rule of its own while lib_layered_config decided it
at deploy time, from a read of its own. The two disagreed in both directions: ``-1``, ``1_0`` and
Arabic-Indic digits passed lsdsk's rule silently and the deploy then refused them, while ``abc``
drew lsdsk's "Using 0o600" warning on a run the deploy went on to refuse - a fallback announced
for a run that never fell back. And a ``--set`` mode, which ``lsdsk config`` lists under the
``cli`` layer as in force, never reached the deploy at all, so the file was written 0600 with
exit 0. The deploy is the one decider now: its refusal is the only thing said about a mode, in
lsdsk's own option names, and a ``--set`` mode is handed to it as an override.
"""

from __future__ import annotations

import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.deploy import deploy_configuration
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production
from lsdsk.domain.deployment import DeployRequest
from lsdsk.domain.enums import DeployTarget

if TYPE_CHECKING:
    from pathlib import Path

USER_FILE_VARIABLE = "LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE"
USER_FILE_KEY = "lib_layered_config.default_permissions.user_file"

#: Asked with ``hasattr`` because Windows has no ``geteuid``. Root's umask and
#: ownership make the deployed modes something other than what was asked for.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

#: Written as escapes: the digits are the point, and they are not ASCII.
ARABIC_INDIC_MODE = "\u0660\u0666\u0660\u0660"


def _the_environment_can_carry(text: str) -> bool:
    """Whether a variable holding ``text`` can be set here at all.

    POSIX encodes the environment with the filesystem encoding, and a single-byte
    locale (``LC_ALL=de_AT.iso88591 PYTHONUTF8=0``) has no byte for an Arabic-Indic
    digit, so ``setenv`` itself raises before the code under test is reached.
    """
    try:
        text.encode(sys.getfilesystemencoding(), "surrogateescape")
    except UnicodeEncodeError:
        return False
    return True


def _deploy(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run the real command through the production wiring; the exit code and stderr, joined to one line."""
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(arguments, services_factory=build_production)
    finally:
        get_config.cache_clear()
    return exit_code, " ".join(capsys.readouterr().err.split())


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param("abc", id="not-octal"),
        pytest.param("-1", id="negative"),
        pytest.param("1_0", id="underscore"),
        pytest.param(
            ARABIC_INDIC_MODE,
            id="arabic-indic-digits",
            marks=pytest.mark.skipif(
                not _the_environment_can_carry(ARABIC_INDIC_MODE),
                reason="this locale's environment encoding has no byte for an Arabic-Indic digit",
            ),
        ),
        pytest.param("0o77777777", id="out-of-range"),
        pytest.param("0o777", id="unsafe"),
        pytest.param("493", id="bare-integer"),
    ],
)
def test_a_mode_the_deploy_refuses_is_refused_once_and_never_announced_as_a_fallback(
    refused: str,
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What lsdsk says about a mode is what the deploy does with it, for every value."""
    monkeypatch.setenv(USER_FILE_VARIABLE, refused)

    exit_code, said = _deploy(["config-deploy", "--target", "user"], capsys)

    assert exit_code == ExitCode.INVALID_ARGUMENT, said
    assert f"{USER_FILE_KEY}:" in said, said
    assert f"ignoring {USER_FILE_KEY}" not in said, said
    assert "Using 0o" not in said, said
    assert not (user_config_dir / "config.toml").exists(), "a refused deploy wrote the file"


def test_the_refusal_names_the_options_this_command_has(
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The hint under a refused configured mode names lsdsk's flags, not the library's parameters."""
    monkeypatch.setenv(USER_FILE_VARIABLE, "abc")

    exit_code, said = _deploy(["config-deploy", "--target", "user"], capsys)

    assert exit_code == ExitCode.INVALID_ARGUMENT, said
    assert "--dir-mode" in said and "--file-mode" in said and "--no-permissions" in said, said
    assert "set_permissions=False" not in said, said
    assert "dir_mode and file_mode" not in said, said


def test_a_mode_the_deploy_accepts_deploys_without_a_word_about_it(
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The liveness control for the refusals above: an accepted value runs to exit 0 and writes the file."""
    monkeypatch.setenv(USER_FILE_VARIABLE, "0o640")

    exit_code, said = _deploy(["config-deploy", "--target", "user"], capsys)

    assert exit_code == 0, said
    assert USER_FILE_KEY not in said, said
    assert (user_config_dir / "config.toml").is_file()


@pytest.mark.os_posix
def test_a_set_mode_reaches_the_bits_on_disk(
    user_config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--set`` is what ``lsdsk config`` reports as in force, so it is what the deploy applies."""
    if RUNNING_AS_ROOT:
        pytest.skip("root's deployment modes are not the ones asked for")

    exit_code, said = _deploy(
        ["--set", f"{USER_FILE_KEY}=0o640", "config-deploy", "--target", "user"],
        capsys,
    )

    assert exit_code == 0, said
    written = user_config_dir / "config.toml"
    assert stat.S_IMODE(written.stat().st_mode) == 0o640, oct(stat.S_IMODE(written.stat().st_mode))


@pytest.mark.os_posix
def test_without_the_set_the_same_deploy_writes_the_shipped_mode(
    user_config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control for the test above: 0o640 is not what this deploy writes on its own."""
    if RUNNING_AS_ROOT:
        pytest.skip("root's deployment modes are not the ones asked for")

    exit_code, said = _deploy(["config-deploy", "--target", "user"], capsys)

    assert exit_code == 0, said
    written = user_config_dir / "config.toml"
    assert stat.S_IMODE(written.stat().st_mode) == 0o600, oct(stat.S_IMODE(written.stat().st_mode))


def test_a_set_mode_the_deploy_refuses_is_refused_rather_than_ignored(
    user_config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unsafe ``--set`` mode used to be dropped on the way, so the deploy succeeded at 0o600.

    Checked on every platform: the library validates an override even where it sets no mode.
    """
    exit_code, said = _deploy(
        ["--set", f"{USER_FILE_KEY}=0o777", "config-deploy", "--target", "user"],
        capsys,
    )

    assert exit_code == ExitCode.INVALID_ARGUMENT, said
    assert USER_FILE_KEY in said and "0o777" in said, said
    assert not (user_config_dir / "config.toml").exists(), "a refused deploy wrote the file"


#: Three file modes that differ from each other AND from each layer's shipped one
#: (0o644 for app and host, 0o600 for user), so a target written with another
#: target's mode, or with its own default, cannot pass.
_CONFIGURED_FILE_MODES = {"app": 0o604, "host": 0o640, "user": 0o644}


def _target_of(written: Path, user_directory: Path) -> str:
    """Which target a deployed file belongs to, read off where it was written.

    A host file and its drop-ins sit under ``hosts/`` (``hosts/<name>.toml``,
    ``hosts/<name>.d/``); the app layer's are beside it, outside that directory.
    """
    if written.is_relative_to(user_directory):
        return "user"
    return "host" if "hosts" in written.parts else "app"


@pytest.mark.os_posix
def test_each_target_is_deployed_with_its_own_configured_file_mode(
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deploy reads each layer's own setting; lsdsk no longer reads them on its behalf.

    The app and host roots are the empty system directory the suite points them at,
    so all three targets land under this test's own tree.
    """
    if RUNNING_AS_ROOT:
        pytest.skip("root's deployment modes are not the ones asked for")
    for layer, mode in _CONFIGURED_FILE_MODES.items():
        monkeypatch.setenv(f"LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__{layer.upper()}_FILE", oct(mode))
    get_config.cache_clear()

    deployed = deploy_configuration(
        DeployRequest(targets=(DeployTarget.APP, DeployTarget.HOST, DeployTarget.USER), set_permissions=True)
    )

    modes: dict[str, set[int]] = {}
    for written in deployed:
        modes.setdefault(_target_of(written, user_config_dir), set()).add(stat.S_IMODE(written.stat().st_mode))
    expected = {target: {mode} for target, mode in _CONFIGURED_FILE_MODES.items()}
    assert modes == expected, {target: sorted(map(oct, found)) for target, found in modes.items()}
