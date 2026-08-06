"""CLI config stories: display, JSON format, sections, deploy, profile, generate-examples, redaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result
    from lib_layered_config import Config


@pytest.mark.os_agnostic
def test_when_config_is_invoked_it_displays_configuration(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config command displays configuration."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["config"], obj=production_factory)

    assert result.exit_code == 0
    # With default config (all commented), output may be empty or show only log messages


@pytest.mark.os_agnostic
def test_when_config_is_invoked_with_json_format_it_outputs_json(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config --format json outputs JSON."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["config", "--format", "json"], obj=production_factory)

    assert result.exit_code == 0
    # Use result.stdout to avoid async log messages from stderr
    assert "{" in result.stdout


@pytest.mark.os_agnostic
def test_when_config_is_invoked_with_nonexistent_section_it_fails(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config with nonexistent section returns error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config", "--section", "nonexistent_section_that_does_not_exist"], obj=production_factory
    )

    assert result.exit_code != 0
    assert "not found" in result.stderr


@pytest.mark.os_agnostic
def test_when_config_is_invoked_with_mocked_data_it_displays_sections(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify config displays sections from mocked configuration."""
    factory = config_cli_context(
        {
            "test_section": {
                "setting1": "value1",
                "setting2": 42,
            }
        }
    )

    result: Result = cli_runner.invoke(cli_mod.cli, ["config"], obj=factory)

    assert result.exit_code == 0
    assert "test_section" in result.output
    assert "setting1" in result.output
    assert "value1" in result.output


@pytest.mark.os_agnostic
def test_when_config_deploy_is_invoked_without_target_it_fails(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-deploy without --target option fails."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy"], obj=production_factory)

    assert result.exit_code != 0
    assert "Missing option" in result.output or "required" in result.output.lower()


@pytest.mark.os_agnostic
def test_when_config_deploy_is_invoked_it_deploys_configuration(
    cli_runner: CliRunner,
    tmp_path: Any,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Verify config-deploy creates configuration files."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        set_permissions: bool = True,
        dir_mode: int | None = None,
        file_mode: int | None = None,
    ) -> list[Path]:
        return [deployed_path]

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 0
    assert "Configuration deployed successfully" in result.output
    assert str(deployed_path) in result.output
    # The output must survive a legacy Windows console codepage (cp1252): a non-ASCII
    # marker here crashes config-deploy with a UnicodeEncodeError on Windows even though
    # the files were already written. Keep the deploy report ASCII-only.
    result.output.encode("cp1252")


@pytest.mark.os_agnostic
def test_when_config_deploy_finds_no_files_to_create_it_informs_user(
    cli_runner: CliRunner,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Verify config-deploy reports when no files are created."""

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        set_permissions: bool = True,
        dir_mode: int | None = None,
        file_mode: int | None = None,
    ) -> list[Path]:
        return []

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 0
    assert "No files were created" in result.output
    assert "--force" in result.output


@pytest.mark.os_agnostic
def test_when_config_deploy_encounters_permission_error_it_handles_gracefully(
    cli_runner: CliRunner,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Verify config-deploy handles PermissionError gracefully."""

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        set_permissions: bool = True,
        dir_mode: int | None = None,
        file_mode: int | None = None,
    ) -> list[Any]:
        raise PermissionError("Permission denied")

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "app"], obj=factory)

    assert result.exit_code != 0
    assert "Permission denied" in result.stderr
    assert "sudo" in result.stderr.lower()


@pytest.mark.os_agnostic
def test_when_config_deploy_supports_multiple_targets(
    cli_runner: CliRunner,
    tmp_path: Any,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Verify config-deploy accepts multiple --target options."""
    from lsdsk.domain.enums import DeployTarget

    path1 = tmp_path / "config1.toml"
    path2 = tmp_path / "config2.toml"
    path1.touch()
    path2.touch()

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        set_permissions: bool = True,
        dir_mode: int | None = None,
        file_mode: int | None = None,
    ) -> list[Path]:
        target_values = [t.value if isinstance(t, DeployTarget) else t for t in targets]
        assert len(target_values) == 2
        assert "user" in target_values
        assert "host" in target_values
        return [path1, path2]

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--target", "host"], obj=factory
    )

    assert result.exit_code == 0
    assert str(path1) in result.output
    assert str(path2) in result.output


@pytest.mark.os_agnostic
def test_when_config_deploy_is_invoked_with_profile_it_passes_profile(
    cli_runner: CliRunner,
    tmp_path: Any,
    inject_deploy_with_profile_capture: Callable[[Path, list[str | None]], Callable[[], Any]],
) -> None:
    """Verify config-deploy passes profile to deploy_configuration."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured_profile: list[str | None] = []

    factory = inject_deploy_with_profile_capture(deployed_path, captured_profile)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--profile", "production"], obj=factory
    )

    assert result.exit_code == 0
    assert captured_profile == ["production"]
    assert "(profile: production)" in result.output


@pytest.mark.os_agnostic
def test_when_config_is_invoked_with_profile_it_passes_profile_to_get_config(
    cli_runner: CliRunner,
    config_factory: Callable[[dict[str, Any]], Config],
    inject_config_with_profile_capture: Callable[[Config, list[str | None]], Callable[[], Any]],
) -> None:
    """Verify config command passes --profile to get_config."""
    captured_profiles: list[str | None] = []
    config = config_factory({"test_section": {"key": "value"}})

    factory = inject_config_with_profile_capture(config, captured_profiles)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config", "--profile", "staging"], obj=factory)

    assert result.exit_code == 0
    assert "staging" in captured_profiles


@pytest.mark.os_agnostic
def test_when_config_is_invoked_without_profile_it_passes_none(
    cli_runner: CliRunner,
    config_factory: Callable[[dict[str, Any]], Config],
    inject_config_with_profile_capture: Callable[[Config, list[str | None]], Callable[[], Any]],
) -> None:
    """Verify config command passes None when no --profile specified."""
    captured_profiles: list[str | None] = []
    config = config_factory({"test_section": {"key": "value"}})

    factory = inject_config_with_profile_capture(config, captured_profiles)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config"], obj=factory)

    assert result.exit_code == 0
    assert None in captured_profiles


@pytest.mark.os_agnostic
def test_when_config_deploy_is_invoked_without_profile_it_passes_none(
    cli_runner: CliRunner,
    tmp_path: Any,
    inject_deploy_with_profile_capture: Callable[[Path, list[str | None]], Callable[[], Any]],
) -> None:
    """Verify config-deploy passes None when no --profile specified."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured_profiles: list[str | None] = []

    factory = inject_deploy_with_profile_capture(deployed_path, captured_profiles)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 0
    assert captured_profiles == [None]
    assert "(profile:" not in result.output


# ======================== Config Display Redaction Tests ========================


@pytest.mark.os_agnostic
def test_when_config_displays_non_sensitive_values_it_shows_them(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Non-sensitive keys must show their real values, not be redacted."""
    factory = config_cli_context(
        {
            "logging": {
                "level": "DEBUG",
                "service": "my_app",
            }
        }
    )

    result: Result = cli_runner.invoke(cli_mod.cli, ["config"], obj=factory)

    assert result.exit_code == 0
    assert "DEBUG" in result.output
    assert "my_app" in result.output
    assert "***REDACTED***" not in result.output


@pytest.mark.os_agnostic
def test_when_config_displays_token_and_secret_keys_it_redacts_them(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Keys containing 'token', 'secret', or 'credential' must be redacted."""
    factory = config_cli_context(
        {
            "auth": {
                "api_token": "tok_abc123",
                "client_secret": "sec_xyz789",
                "username": "admin",
            }
        }
    )

    result: Result = cli_runner.invoke(cli_mod.cli, ["config"], obj=factory)

    assert result.exit_code == 0
    assert "tok_abc123" not in result.output
    assert "sec_xyz789" not in result.output
    assert "admin" in result.output


@pytest.mark.os_agnostic
def test_when_config_generate_examples_is_invoked_it_creates_files(
    cli_runner: CliRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples creates files in the target directory."""
    created_file = tmp_path / "example.toml"
    created_file.touch()

    def mock_generate_examples(
        destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False, platform: str | None = None
    ) -> list[Path]:
        return [created_file]

    monkeypatch.setattr("lsdsk.adapters.cli.commands.config.generate_examples", mock_generate_examples)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-generate-examples", "--destination", str(tmp_path)],
        obj=production_factory,
    )

    assert result.exit_code == 0
    assert "Generated 1 example file(s)" in result.output
    assert str(created_file) in result.output


@pytest.mark.os_agnostic
def test_when_config_generate_examples_has_no_files_it_informs_user(
    cli_runner: CliRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples reports when all files already exist."""

    def mock_generate_examples(
        destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False, platform: str | None = None
    ) -> list[Path]:
        return []

    monkeypatch.setattr("lsdsk.adapters.cli.commands.config.generate_examples", mock_generate_examples)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-generate-examples", "--destination", str(tmp_path)],
        obj=production_factory,
    )

    assert result.exit_code == 0
    assert "No files generated" in result.output
    assert "--force" in result.output


@pytest.mark.os_agnostic
def test_when_config_generate_examples_missing_destination_it_fails(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples without --destination fails."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["config-generate-examples"], obj=production_factory)

    assert result.exit_code != 0
    assert "Missing option" in result.output or "required" in result.output.lower()


@pytest.mark.os_agnostic
def test_when_config_generate_examples_with_force_it_passes_force_flag(
    cli_runner: CliRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples passes --force flag to generate_examples."""
    captured_force: list[bool] = []
    created_file = tmp_path / "example.toml"
    created_file.touch()

    def mock_generate_examples(
        destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False, platform: str | None = None
    ) -> list[Path]:
        captured_force.append(force)
        return [created_file]

    monkeypatch.setattr("lsdsk.adapters.cli.commands.config.generate_examples", mock_generate_examples)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-generate-examples", "--destination", str(tmp_path), "--force"],
        obj=production_factory,
    )

    assert result.exit_code == 0
    assert captured_force == [True]


@pytest.mark.os_agnostic
def test_when_config_generate_examples_without_force_it_defaults_to_false(
    cli_runner: CliRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples defaults force=False."""
    captured_force: list[bool] = []
    created_file = tmp_path / "example.toml"
    created_file.touch()

    def mock_generate_examples(
        destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False, platform: str | None = None
    ) -> list[Path]:
        captured_force.append(force)
        return [created_file]

    monkeypatch.setattr("lsdsk.adapters.cli.commands.config.generate_examples", mock_generate_examples)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-generate-examples", "--destination", str(tmp_path)],
        obj=production_factory,
    )

    assert result.exit_code == 0
    assert captured_force == [False]


@pytest.mark.os_agnostic
def test_when_config_generate_examples_encounters_error_it_exits_with_general_error(
    cli_runner: CliRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples handles exceptions gracefully."""

    def mock_generate_examples(
        destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False, platform: str | None = None
    ) -> list[Path]:
        raise OSError("Disk full")

    monkeypatch.setattr("lsdsk.adapters.cli.commands.config.generate_examples", mock_generate_examples)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-generate-examples", "--destination", str(tmp_path)],
        obj=production_factory,
    )

    assert result.exit_code == 1  # GENERAL_ERROR
    assert "Disk full" in result.stderr


@pytest.mark.os_agnostic
def test_when_config_generate_examples_it_passes_correct_metadata(
    cli_runner: CliRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], Any],
) -> None:
    """Verify config-generate-examples passes correct slug, vendor, app from __init__conf__."""
    from lsdsk import __init__conf__

    captured_params: list[dict[str, Any]] = []
    created_file = tmp_path / "example.toml"
    created_file.touch()

    def mock_generate_examples(
        destination: str | Path, *, slug: str, vendor: str, app: str, force: bool = False, platform: str | None = None
    ) -> list[Path]:
        captured_params.append({"slug": slug, "vendor": vendor, "app": app, "destination": str(destination)})
        return [created_file]

    monkeypatch.setattr("lsdsk.adapters.cli.commands.config.generate_examples", mock_generate_examples)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-generate-examples", "--destination", str(tmp_path)],
        obj=production_factory,
    )

    assert result.exit_code == 0
    assert len(captured_params) == 1
    assert captured_params[0]["slug"] == __init__conf__.LAYEREDCONF_SLUG
    assert captured_params[0]["vendor"] == __init__conf__.LAYEREDCONF_VENDOR
    assert captured_params[0]["app"] == __init__conf__.LAYEREDCONF_APP
    assert captured_params[0]["destination"] == str(tmp_path)


@pytest.mark.os_agnostic
def test_when_config_subcommand_profile_reloads_it_preserves_root_set_overrides(
    cli_runner: CliRunner,
    config_factory: Callable[[dict[str, Any]], Config],
    clear_config_cache: None,
) -> None:
    """Root --set overrides must be reapplied when config --profile reloads config.

    When a user invokes:
        cli --set section.key=override config --profile test

    The subcommand-level profile triggers a config reload. The root-level
    --set overrides must be reapplied to the new config.

    Injected at the ``AppServices`` port rather than by patching
    ``loader.get_config``: the composition root binds that name at import time,
    so a patch on the loader module cannot reach the function the CLI calls, and
    the test would silently exercise this machine's real configuration instead.
    """
    from dataclasses import replace

    from lsdsk.composition import build_production

    base_config = config_factory({"section": {"key": "original"}})

    # Two calls are expected: root.py loads once, then config.py reloads for the
    # subcommand profile. Counting them is what proves the reload happened at
    # all - without it, a CLI that ignored --profile entirely would still show
    # the override and pass.
    seen_profiles: list[str | None] = []

    def fake_get_config(*, profile: str | None = None, **_kwargs: Any) -> Config:
        seen_profiles.append(profile)
        return base_config

    services = replace(build_production(), get_config=fake_get_config)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "section.key=overridden", "config", "--profile", "test", "--format", "json"],
        obj=lambda: services,
    )

    assert result.exit_code == 0
    assert seen_profiles == [None, "test"], f"the profile reload did not happen: {seen_profiles}"
    assert "overridden" in result.stdout
    assert '"original"' not in result.stdout


@pytest.mark.os_agnostic
def test_when_config_subcommand_has_no_profile_it_uses_stored_config_with_overrides(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Without subcommand --profile, config uses already-overridden config from context."""
    factory = config_cli_context({"section": {"key": "original"}})

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "section.key=overridden", "config", "--format", "json"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "overridden" in result.stdout
    assert '"original"' not in result.stdout


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "section",
    ["lib_log_rich", "lib_log_rich.console_theme", "", None],
    ids=["top level", "dotted path", "empty string", "unset"],
)
def test_the_section_filter_selects_the_same_thing_in_both_formats(
    section: str | None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Two renderings of one selection must not disagree about what was selected.

    The structured path resolved a top-level key while the human path resolved a
    dotted one through the configuration library, so `--section a.b` printed the
    value under `--format human` and exited 22 under `--format json`, and an
    empty string meant "everything" to one and "no such section" to the other.
    """
    args = ["config"] if section is None else ["config", "--section", section]
    human: Result = cli_runner.invoke(cli_mod.cli, args, obj=production_factory)
    structured: Result = cli_runner.invoke(cli_mod.cli, [*args, "--format", "json"], obj=production_factory)

    assert human.exit_code == structured.exit_code, (
        f"--section {section!r} exits {human.exit_code} as human and {structured.exit_code} as json"
    )
    assert human.exit_code == 0, "the control: this selection is meant to succeed"


@pytest.mark.os_agnostic
def test_a_missing_section_reads_the_same_in_both_formats(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Same exit code and same sentence, so a wrapper can match on either."""
    args = ["config", "--section", "no_such_section"]
    human: Result = cli_runner.invoke(cli_mod.cli, args, obj=production_factory)
    structured: Result = cli_runner.invoke(cli_mod.cli, [*args, "--format", "json"], obj=production_factory)

    assert human.exit_code == structured.exit_code == 22
    assert "not found" in human.stderr
    assert "not found" in structured.stderr


@pytest.mark.os_agnostic
def test_a_rejected_profile_is_a_clean_argument_error_not_a_traceback(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """A bad --profile is user input, so it must not surface as an unhandled exception.

    The configuration library validates the name and raises ValueError before it
    writes anything. Narrowing the deploy handler to OSError once let that
    escape to the top-level handler, which printed a raw exception line.
    """
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-deploy", "--target", "user", "--profile", "bad profile!!"],
        obj=production_factory,
    )

    assert result.exit_code == 22
    assert "ValueError" not in result.output, "the exception type leaked to the user"
    assert "Error:" in result.stderr


# --------------------------------------------------------------------------
# A secret is hidden by what its name means, not by how it was spelled
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("output_format", ["human", "json"])
def test_a_camel_case_secret_is_redacted_in_both_modes(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    """The library's pattern needs an underscore boundary; real keys do not have one.

    Measured against its own ``is_sensitive``: ``smtp_password`` and ``api_key``
    redact, while ``SmtpPassword``, ``dbPassword``, ``db_pass``, ``PASS``,
    ``Authorization`` and ``privatekey`` do not. ``lsdsk config`` states secret
    safety as its own guarantee, in both modes, so it cannot rest entirely on
    somebody else's regex.
    """
    from lsdsk.adapters.cli import cli

    home = tmp_path / "xdg" / "lsdsk"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        "[alerting]\n"
        'SmtpPassword = "hunter2-super-secret"\n'
        'db_pass = "another-secret-value"\n'
        'webhook_Authorization = "Bearer abcdef123456"\n'
        'password = "properly-named-secret"\n'
        'host = "mail.example.com"\n',
        encoding="utf-8",
    )
    # The loader is cached, so a Config read by an earlier test in this file
    # would be reused and this would silently assert against the wrong file.
    from lsdsk.adapters.config.loader import get_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    get_config.cache_clear()
    try:
        result = cli_runner.invoke(
            cli, ["config", "--section", "alerting", "--format", output_format], obj=production_factory
        )
    finally:
        get_config.cache_clear()

    assert "mail.example.com" in result.output, "the section did not render, so this asserted nothing"
    for secret in ("hunter2-super-secret", "another-secret-value", "abcdef123456", "properly-named-secret"):
        assert secret not in result.output, f"{output_format}: {secret!r} reached the output"
