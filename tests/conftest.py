"""Shared pytest fixtures for CLI and module-entry tests.

Centralizes test infrastructure following clean architecture principles:
- All shared fixtures live here
- Tests import fixtures implicitly via pytest's conftest discovery
- Fixtures use descriptive names that read as plain English
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
from dataclasses import fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import lib_cli_exit_tools
import pytest
from click.testing import CliRunner
from lib_layered_config import Config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from lib_layered_config.domain.config import SourceInfo


from lsdsk.composition import AppServices, build_production

_COVERAGE_BASENAME = ".coverage.lsdsk"


def _purge_stale_coverage_files(cov_path: Path) -> None:
    """Delete leftover SQLite database and journal files from crashed runs.

    A prior crash can leave ``-journal``, ``-wal``, or ``-shm`` sidecar
    files next to the coverage database.  SQLite interprets those as an
    incomplete transaction and may raise ``database is locked`` on the
    next open.

    Note:
        We use an explicit suffix list rather than glob (``cov_path.parent.glob(f"{cov_path.name}*")``)
        because glob could match unrelated files sharing the same prefix. The SQLite WAL-mode
        sidecar suffixes are well-documented and stable across versions.
    """
    for suffix in ("", "-journal", "-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            Path(str(cov_path) + suffix).unlink()


def pytest_configure(config: pytest.Config) -> None:
    """Redirect the coverage database to a **local** temp directory.

    coverage.py stores trace data in a SQLite database.  SQLite requires
    POSIX file-locking semantics that network mounts (SMB / NFS) do not
    reliably provide, and stale journal files from a previous crash can
    trigger *"database is locked"* on Python 3.14's free-threaded build.

    This hook runs **before** ``pytest-cov``'s ``pytest_sessionstart``
    creates the ``Coverage()`` object, so the ``COVERAGE_FILE`` value is
    picked up regardless of how pytest is invoked (CI, ``make test``,
    bare ``pytest --cov``).
    """
    if "COVERAGE_FILE" not in os.environ:
        cov_path = Path(tempfile.gettempdir()) / _COVERAGE_BASENAME
        _purge_stale_coverage_files(cov_path)
        os.environ["COVERAGE_FILE"] = str(cov_path)


# Which platforms each os_* marker declares its test valid on. A marker that is
# only registered in pyproject silences the unknown-marker warning and skips
# NOTHING, so a test reads as guarded while running everywhere - which is how
# three POSIX-only tests came to be selected on the Windows runner.
PLATFORM_MARKERS: dict[str, Callable[[str], bool]] = {
    "os_posix": lambda platform: platform != "win32",
    "os_linux": lambda platform: platform.startswith("linux"),
    "os_macos": lambda platform: platform == "darwin",
    "os_windows": lambda platform: platform == "win32",
}


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a test whose platform marker does not match this machine.

    Wired for the whole marker family rather than for the one test that was
    failing, because the next marker added would otherwise be decorative too.
    ``os_agnostic`` is deliberately absent: it asserts the test runs everywhere,
    so it has nothing to skip on.

    Args:
        item: The test about to run.
    """
    # sys.platform is read here rather than captured at import time so a test
    # monkeypatching it cannot change which tests are selected.
    import sys

    for marker, is_supported in PLATFORM_MARKERS.items():
        if marker in item.keywords and not is_supported(sys.platform):
            pytest.skip(f"{marker}: not applicable on {sys.platform}")


@pytest.fixture(autouse=True)
def counter_history_stays_out_of_the_real_store(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give every test its own state directory, so none can reach the real store.

    Without this the suite reads - and an ordinary run WRITES - the developer's
    own ``~/.local/state/lsdsk/history.json``, so what a test sees depends on
    which machine it runs on and on what earlier runs left behind. It surfaced
    when the fixtures were renamed: the real store still held the previous
    hostname, the tool correctly refused to mix two machines, and the terminal
    width tests failed on the length of that warning rather than on anything
    they were testing.

    The three environment variables are redirected because
    ``default_history_path`` reads exactly those and nothing else. Setting the
    ``[history] path`` key or its environment variable instead would isolate the
    store equally well and outrank the configuration under test in every test
    that sets one, which is the layer several of them exist to check. A test
    that redirects these itself still wins, because its own monkeypatch runs
    after this fixture.
    """
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))  # Linux
    monkeypatch.setenv("LOCALAPPDATA", str(state))  # Windows
    monkeypatch.setenv("HOME", str(state))  # macOS, and the fallback on both others


def _load_dotenv() -> None:
    """Load .env file when it exists for integration test configuration."""
    try:
        from dotenv import load_dotenv

        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)
    except ImportError:
        pass


_load_dotenv()

ANSI_ESCAPE_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
CONFIG_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(type(lib_cli_exit_tools.config)))


def _remove_ansi_codes(text: str) -> str:
    """Return *text* stripped of ANSI escape sequences."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


def _snapshot_cli_config() -> dict[str, object]:
    """Capture every attribute from ``lib_cli_exit_tools.config``."""
    return {name: getattr(lib_cli_exit_tools.config, name) for name in CONFIG_FIELDS}


def _restore_cli_config(snapshot: dict[str, object]) -> None:
    """Reapply a configuration snapshot captured by ``_snapshot_cli_config``."""
    for name, value in snapshot.items():
        setattr(lib_cli_exit_tools.config, name, value)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a fresh CliRunner per test.

    Click 8.x provides separate result.stdout and result.stderr attributes.
    Use result.stdout for clean output (e.g., JSON parsing) to avoid
    async log messages from stderr contaminating the output.

    Returns:
        CliRunner: A fresh Click test runner instance.

    Example:
        def test_help(cli_runner: CliRunner) -> None:
            result = cli_runner.invoke(cli, ["--help"])
            assert result.exit_code == 0
    """
    return CliRunner()


@pytest.fixture
def production_factory() -> Callable[[], AppServices]:
    """Provide the production services factory for tests.

    Use this when invoking CLI commands that don't need custom injection.
    Returns the ``build_production`` factory which wires real adapters.

    Returns:
        Callable[[], AppServices]: Factory returning production-wired AppServices.

    Example:
        def test_info(cli_runner: CliRunner, production_factory: Callable[[], AppServices]) -> None:
            result = cli_runner.invoke(cli, ["info"], obj=production_factory)
            assert result.exit_code == 0
    """

    return build_production


@pytest.fixture
def strip_ansi() -> Callable[[str], str]:
    """Return a helper that strips ANSI escape sequences from a string.

    Useful for comparing CLI output that may contain rich formatting
    (colors, bold, etc.) against expected plain text.

    Returns:
        Callable[[str], str]: Function that removes ANSI codes from input.

    Example:
        def test_output(cli_runner: CliRunner, strip_ansi: Callable[[str], str]) -> None:
            result = cli_runner.invoke(cli, ["info"])
            plain = strip_ansi(result.output)
            assert "version" in plain
    """

    def _strip(value: str) -> str:
        return _remove_ansi_codes(value)

    return _strip


@pytest.fixture
def managed_traceback_state() -> Iterator[None]:
    """Reset traceback flags to a known baseline and restore after the test.

    Combines the responsibilities of the former ``isolated_traceback_config``
    (reset to clean state) and ``preserve_traceback_state`` (snapshot/restore)
    into a single fixture.  Use this whenever a test reads or mutates the
    global ``lib_cli_exit_tools.config`` traceback flags.

    Yields:
        None: Test runs with isolated traceback state.

    Example:
        def test_traceback_flag(managed_traceback_state: None) -> None:
            lib_cli_exit_tools.config.traceback = True
            # State automatically restored after test
    """
    lib_cli_exit_tools.reset_config()
    lib_cli_exit_tools.config.traceback = False
    lib_cli_exit_tools.config.traceback_force_color = False
    snapshot = _snapshot_cli_config()
    try:
        yield
    finally:
        _restore_cli_config(snapshot)


@pytest.fixture
def clear_config_cache() -> Iterator[None]:
    """Clear the get_config lru_cache before each test.

    Note: Only clears before, not after, to avoid errors when the function
    has been monkeypatched during the test (losing cache_clear method).

    Yields:
        None: Test runs with cleared config cache.

    Example:
        def test_config_reload(clear_config_cache: None) -> None:
            config1 = get_config()
            # Cache was cleared, so this is a fresh load
    """
    from lsdsk.adapters.config import loader as config_mod

    config_mod.get_config.cache_clear()
    yield


@pytest.fixture
def inject_config(
    clear_config_cache: None,
) -> Callable[[Config], Callable[[], AppServices]]:
    """Return a factory that provides test services with injected Config.

    Creates a services factory with the injected config loader,
    avoiding filesystem I/O while exercising the real Config API.
    Only replaces the I/O boundary (``get_config``), not the Config object itself.

    Args:
        clear_config_cache: Implicit fixture dependency ensuring cache is cleared.

    Returns:
        Callable[[Config], Callable[[], AppServices]]: Function that accepts a Config
            and returns a services factory callable suitable for ``cli_runner.invoke(obj=...)``.

    Example:
        def test_config_display(
            cli_runner: CliRunner,
            config_factory: Callable[[dict[str, Any]], Config],
            inject_config: Callable[[Config], Callable[[], AppServices]],
        ) -> None:
            config = config_factory({"section": {"key": "value"}})
            factory = inject_config(config)
            result = cli_runner.invoke(cli, ["config"], obj=factory)
            assert "key" in result.output
    """

    def _inject(config: Config) -> Callable[[], AppServices]:
        def _fake_get_config(**_kwargs: Any) -> Config:
            return config

        prod = build_production()
        test_services = replace(prod, get_config=_fake_get_config)
        return lambda: test_services

    return _inject


@pytest.fixture
def inject_config_with_profile_capture(
    clear_config_cache: None,
) -> Callable[[Config, list[str | None]], Callable[[], AppServices]]:
    """Return a factory that captures profile arguments during get_config.

    Creates a services factory with a get_config that records profile
    arguments for assertion in tests verifying --profile propagation.

    Args:
        clear_config_cache: Implicit fixture dependency ensuring cache is cleared.

    Returns:
        Callable[[Config, list[str | None]], Callable[[], AppServices]]: Function
            that accepts (Config, capture_list) and returns a services factory.
            Profile values passed to get_config are appended to capture_list.

    Example:
        def test_profile_passed(
            cli_runner: CliRunner,
            config_factory: Callable[[dict[str, Any]], Config],
            inject_config_with_profile_capture: Callable[..., Callable[[], AppServices]],
        ) -> None:
            captured: list[str | None] = []
            config = config_factory({})
            factory = inject_config_with_profile_capture(config, captured)
            cli_runner.invoke(cli, ["--profile", "staging", "config"], obj=factory)
            assert captured == ["staging"]
    """

    def _inject(config: Config, captured_profiles: list[str | None]) -> Callable[[], AppServices]:
        def _capturing_get_config(*, profile: str | None = None, **_kwargs: Any) -> Config:
            captured_profiles.append(profile)
            return config

        prod = build_production()
        test_services = replace(prod, get_config=_capturing_get_config)
        return lambda: test_services

    return _inject


@pytest.fixture
def inject_deploy_with_profile_capture(
    clear_config_cache: None,
) -> Callable[[Path, list[str | None]], Callable[[], AppServices]]:
    """Return a factory with deploy_configuration that captures profile arguments.

    Creates a services factory with a deploy_configuration that records
    profile arguments for assertion in tests verifying --profile propagation
    to deployment operations.

    Args:
        clear_config_cache: Implicit fixture dependency ensuring cache is cleared.

    Returns:
        Callable[[Path, list[str | None]], Callable[[], AppServices]]: Function
            that accepts (deployed_path, capture_list) and returns a services factory.
            The fake deploy always returns [deployed_path] and appends profile to capture_list.

    Example:
        def test_deploy_profile(
            cli_runner: CliRunner,
            tmp_path: Path,
            inject_deploy_with_profile_capture: Callable[..., Callable[[], AppServices]],
        ) -> None:
            captured: list[str | None] = []
            factory = inject_deploy_with_profile_capture(tmp_path / "config.toml", captured)
            cli_runner.invoke(cli, ["--profile", "prod", "config-deploy", ...], obj=factory)
            assert captured == ["prod"]
    """

    def _inject(deployed_path: Path, captured_profiles: list[str | None]) -> Callable[[], AppServices]:
        def _capturing_deploy(
            *,
            targets: Any,
            force: bool = False,
            profile: str | None = None,
            set_permissions: bool = True,
            dir_mode: int | None = None,
            file_mode: int | None = None,
        ) -> list[Path]:
            captured_profiles.append(profile)
            return [deployed_path]

        prod = build_production()
        test_services = replace(prod, deploy_configuration=_capturing_deploy)
        return lambda: test_services

    return _inject


@pytest.fixture
def inject_deploy_configuration() -> Callable[[Callable[..., list[Path]]], Callable[[], AppServices]]:
    """Return a factory with a custom deploy_configuration function.

    Creates a services factory with the provided deploy_configuration
    function while keeping other services as production. Use this for
    testing deploy behavior with custom implementations (mocks, spies).

    Returns:
        Callable[[Callable[..., list[Path]]], Callable[[], AppServices]]: Function
            that accepts a deploy function and returns a services factory.

    Example:
        def test_deploy_called(
            cli_runner: CliRunner,
            inject_deploy_configuration: Callable[..., Callable[[], AppServices]],
        ) -> None:
            calls = []
            def spy_deploy(**kwargs) -> list[Path]:
                calls.append(kwargs)
                return [Path("/fake/path")]
            factory = inject_deploy_configuration(spy_deploy)
            cli_runner.invoke(cli, ["config-deploy", "--target", "user"], obj=factory)
            assert len(calls) == 1
    """

    def _inject(deploy_fn: Callable[..., list[Path]]) -> Callable[[], AppServices]:
        prod = build_production()
        test_services = replace(prod, deploy_configuration=deploy_fn)
        return lambda: test_services

    return _inject


@pytest.fixture
def config_cli_context(
    clear_config_cache: None,
) -> Callable[[dict[str, Any]], Callable[[], AppServices]]:
    """Create CLI test context with injected config.

    Combines config creation and injection into a single fixture.
    Simpler than ``inject_config`` when you don't need a pre-built Config object.

    Args:
        clear_config_cache: Implicit fixture dependency ensuring cache is cleared.

    Returns:
        Callable[[dict[str, Any]], Callable[[], AppServices]]: Function that takes
            a config dict and returns a services factory for CLI invocation.

    Example:
        def test_config_display(
            cli_runner: CliRunner,
            config_cli_context: Callable[[dict[str, Any]], Callable[[], AppServices]],
        ) -> None:
            factory = config_cli_context({"section": {"key": "value"}})
            result = cli_runner.invoke(cli, ["config"], obj=factory)
            assert "key" in result.output
    """

    def _create(config_data: dict[str, Any]) -> Callable[[], AppServices]:
        config = Config(config_data, {})
        prod = build_production()

        def _fake_get_config(**_kwargs: Any) -> Config:
            return config

        test_services = replace(prod, get_config=_fake_get_config)
        return lambda: test_services

    return _create


@pytest.fixture
def config_factory() -> Callable[[dict[str, Any]], Config]:
    """Build real Config objects from plain dicts, with no filesystem access.

    Tests use a real Config rather than a mock so they exercise the same
    lookup and provenance behaviour the application does.

    Returns:
        A factory taking the config data and returning a Config.

    Example:
        def test_thresholds(config_factory: Callable[[dict[str, Any]], Config]) -> None:
            config = config_factory({"wear": {"warning_percent": 80}})
            assert config.get("wear.warning_percent") == 80
    """

    def _build(data: dict[str, Any]) -> Config:
        return Config(data, {})

    return _build


@pytest.fixture
def source_info_factory() -> Callable[..., SourceInfo]:
    """Build provenance records without coupling tests to the TypedDict shape.

    Returns:
        A factory taking a key, a layer name and a path, returning SourceInfo.
    """

    def _build(key: str, layer: str, path: str) -> SourceInfo:
        return cast("SourceInfo", {"key": key, "layer": layer, "path": path})

    return _build


@pytest.fixture
def rendered() -> Callable[..., str]:
    """Provide a helper that returns what a Rich renderable prints."""
    return _render_to_text


def _render_to_text(renderable: object, width: int = 200) -> str:
    """Return what a Rich renderable prints, as plain text.

    Renderers return whatever shape suits the content: a bare ``Text`` when
    there is one line, a ``Group`` once caveats or headers join it. Reaching for
    ``.plain`` therefore passes on one machine and raises ``AttributeError`` on
    another, which is a property of the test rather than of the code. Printing
    it is what the user sees, so that is what an assertion should read.

    Args:
        renderable: Anything Rich can print.
        width: Console width, wide by default so assertions are not defeated by
            a column being dropped to fit.

    Returns:
        The rendered text.
    """
    from rich.console import Console

    console = Console(width=width, record=True, file=io.StringIO(), legacy_windows=False)
    console.print(renderable)
    return console.export_text()


# --------------------------------------------------------------------------
# Real-hardware coverage reporting
# --------------------------------------------------------------------------
#
# Lives here rather than in tests/e2e/conftest.py: tests/ is not a package, so
# pytest imports every conftest as the top-level module `conftest`, and a second
# one shadows this file - which test_platform_markers.py imports by name.
#
# The report itself exists because pytest captures stdout for a PASSING test, so
# a print inside one is visible only when it fails, which is backwards for
# evidence. And a run where no host was reachable skips every test and still
# renders green: `make testintegration` emits five lines ending in a
# "result":"pass" envelope, in which a sweep of six machines and a sweep of none
# look identical. pytest_terminal_summary runs in the reporting phase and is not
# captured, so the numbers appear either way.


#: Filled by the real-hardware tests as they go, one entry per host attempted.
#: They live here rather than beside those tests because `tests/` is not a
#: package: pytest imports every conftest as the top-level module `conftest`, so
#: a second one in `tests/e2e/` shadows this file, and `test_platform_markers.py`
#: imports this one by name.
PROBED: list[dict[str, Any]] = []

#: Hosts that could not be reached, so a green run names the coverage it lost.
SKIPPED: list[str] = []

#: Where the coverage is also WRITTEN, because the terminal is not a reliable
#: channel: `make testintegration` runs pytest inside bmk, which captures the
#: whole run and emits only its own {"result":"pass"} envelope, so the summary
#: below never reaches the operator through the documented entry point. A file
#: survives any capture, and `make test` leaves the previous one alone.
COVERAGE_FILE = Path(__file__).parent.parent / "e2e-coverage.json"


def record_probe(facts: dict[str, Any]) -> None:
    """Record one host's measurements for the end-of-run summary."""
    PROBED.append(facts)


def record_skip(host: str, reason: str) -> None:
    """Record that a host was not probed, and why."""
    SKIPPED.append(f"{host}: {reason}")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Report the coverage a hardware run achieved, whatever the outcome."""
    probed, skipped_hosts = PROBED, SKIPPED
    if not probed and not skipped_hosts:
        return

    write = terminalreporter.write_line
    write("")
    write("real-hardware coverage")
    for facts in probed:
        write(
            f"  {facts.get('host', '?'):18s} {facts.get('platform')!s:8s} "
            f"checks={facts.get('checks'):<4} disks={facts.get('disks'):<3} "
            f"buses={facts.get('by_bus')} privileged={facts.get('privileged')} "
            f"healthy={facts.get('healthy_buses')} blind={facts.get('blind_buses')}"
        )
    for skip in skipped_hosts:
        write(f"  SKIPPED {skip}")

    drives = sum(int(facts.get("disks") or 0) for facts in probed)
    checks = sum(int(facts.get("checks") or 0) for facts in probed)
    platforms = sorted({str(facts.get("platform")) for facts in probed})
    write(f"  {len(probed)} host(s) probed, {len(skipped_hosts)} skipped: {checks} checks over {drives} drives")

    # The line that separates a real sweep from a sweep of nothing. Every host
    # being unreachable passes every test by skipping it, and without this the
    # terminal says the same thing either way.
    if not probed:
        write("  NO HOST WAS PROBED - this run proves nothing about real hardware")
    elif "Windows" not in platforms:
        write("  NO WINDOWS HOST WAS PROBED - SetupAPI and DeviceIoControl are untested in this run")

    summary = {
        "hosts_probed": len(probed),
        "hosts_skipped": len(skipped_hosts),
        "checks": checks,
        "drives": drives,
        "platforms": platforms,
        "windows_covered": "Windows" in platforms,
        "hosts": probed,
        "skipped": skipped_hosts,
    }
    with contextlib.suppress(OSError):
        COVERAGE_FILE.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        write(f"  written to {COVERAGE_FILE}")
