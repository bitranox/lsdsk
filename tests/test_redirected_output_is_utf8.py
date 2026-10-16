"""Output written to a file or a pipe is UTF-8, whatever the console code page or locale.

RFC 8259 requires JSON exchanged between systems to be UTF-8, and a redirected
stream is exactly that exchange. Python encodes a stream that is not a terminal
in the LOCALE's encoding unless UTF-8 mode is on, so ``lsdsk disks --format json
> out.json`` was cp1252 on a Windows machine and Latin-1 or ASCII on a Linux one
with such a locale: a UTF-8 consumer misread the bytes, and a character outside
the code page was lost as ``?`` before anybody could read it. Windows device
descriptions are localised, so this is ordinary data on a German machine rather
than a crafted case.

A terminal keeps its own encoding, because what it renders is somebody reading
it, and an explicit ``PYTHONIOENCODING`` is a request this tool does not
override.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lsdsk.adapters.cli.main import main

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from lsdsk.composition import AppServices

REPO = Path(__file__).parent.parent
CAPTURE = REPO / "tests" / "fixtures" / "hw" / "linux-minimal.json"

#: Latin-1 has the first two letters and not the euro sign; cp1252 has all three
#: and encodes them as bytes that are not UTF-8; ASCII has none of them.
HOSTNAME = "grüße-€"


class _Terminal(io.TextIOWrapper):
    """A stream that answers as a terminal does, for the arm that must be left alone."""

    def isatty(self) -> bool:
        return True


def _capture_named(tmp_path: Path) -> Path:
    """Write a committed capture back out with a non-ASCII hostname, which reaches the JSON output."""
    capture: dict[str, Any] = json.loads(CAPTURE.read_text(encoding="utf-8"))
    capture["hostname"] = HOSTNAME
    target = tmp_path / "capture.json"
    target.write_bytes(json.dumps(capture).encode("utf-8"))
    return target


def _disks_as_json(tmp_path: Path) -> list[str]:
    return [
        "--no-record",
        "--history-file",
        str(tmp_path / "history.json"),
        "--replay",
        str(_capture_named(tmp_path)),
        "disks",
        "--format",
        "json",
    ]


def _hostname_in(payload: bytes) -> str:
    """Decode stdout as UTF-8, strictly, and return the hostname the envelope carries."""
    envelope: dict[str, Any] = json.loads(payload.decode("utf-8"))
    return str(envelope["data"]["hostname"])


def test_the_real_entry_point_writes_utf8_into_a_pipe_under_a_legacy_locale(tmp_path: Path) -> None:
    """The console script, with UTF-8 mode off and a locale that is not UTF-8.

    ``LC_ALL=C`` with locale coercion off makes a POSIX child's stdout ASCII; on
    Windows the locale variables are not read and a pipe is the ANSI code page.
    Either way the unfixed tool wrote the hostname in something other than UTF-8.
    """
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
    environment.update({"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "TERM": "dumb"})

    done = subprocess.run(  # noqa: S603 - argv is built here, no shell
        [sys.executable, "-X", "utf8=0", "-m", "lsdsk", *_disks_as_json(tmp_path)],
        capture_output=True,
        cwd=str(REPO),
        env=environment,
        check=False,
        timeout=120,
    )

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert _hostname_in(done.stdout) == HOSTNAME


def test_a_redirected_stdout_in_a_legacy_encoding_receives_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """The same, in process, on every platform: a Latin-1 stream that is not a terminal."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="latin-1", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)

    code = main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert code == 0
    assert _hostname_in(buffer.getvalue()) == HOSTNAME


def test_the_stream_gets_its_own_encoding_back_once_the_run_is_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """``main`` returns rather than exits, so what it did to the stream would outlive it."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    stream = io.TextIOWrapper(io.BytesIO(), encoding="latin-1", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)

    main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert (stream.encoding, stream.errors) == ("latin-1", "strict")


def test_a_redirected_stderr_in_a_legacy_encoding_receives_utf8(
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """A diagnostic is read by a program too when stderr is redirected."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(buffer, encoding="latin-1", errors="strict", newline=""))

    code = main(["--set", f"display.{HOSTNAME}=1", "disks"], services_factory=production_factory)

    assert code == 2
    assert HOSTNAME in buffer.getvalue().decode("utf-8")


def test_a_terminal_keeps_its_own_encoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """What a terminal renders is read by a person, in whatever the terminal was set up for."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", _Terminal(buffer, encoding="latin-1", errors="strict", newline=""))

    main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert b'"gr\xfc\xdfe-?"' in buffer.getvalue()


def test_an_encoding_named_in_pythonioencoding_is_left_as_asked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """``PYTHONIOENCODING`` is somebody asking for an encoding, which is not this tool's to overrule."""
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="latin-1", errors="strict", newline=""))

    main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert b'"gr\xfc\xdfe-?"' in buffer.getvalue()


def test_a_usage_error_a_terminal_cannot_encode_degrades_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """Click prints its own usage errors, past the sink that degrades what a console cannot encode.

    A terminal keeps its encoding, so a Latin-1 one is still handed the refusal as written, and
    a character it lacks raised ``UnicodeEncodeError`` out of ``main`` instead of the exit 2
    the refusal means.
    """
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stderr", _Terminal(buffer, encoding="latin-1", errors="strict", newline=""))

    code = main(["--set", f"display.{HOSTNAME}=1", "disks"], services_factory=production_factory)

    assert code == 2
    assert "display.grüße-?" in buffer.getvalue().decode("latin-1")


class _CannotSay(io.TextIOWrapper):
    """A stream whose ``isatty`` fails, as one on a closed or detached descriptor can."""

    def isatty(self) -> bool:
        raise OSError("isatty refused")


class _RecordingErrors(io.TextIOWrapper):
    """A stream that remembers the encoding and error handler in force at every write."""

    def __init__(self, buffer: io.BytesIO, *, encoding: str, errors: str) -> None:
        super().__init__(buffer, encoding=encoding, errors=errors, newline="")
        self.seen: list[tuple[str, str | None]] = []

    def write(self, text: str) -> int:
        self.seen.append((self.encoding, self.errors))
        return super().write(text)


def test_an_error_handler_alone_in_pythonioencoding_is_not_an_encoding_asked_for(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """``PYTHONIOENCODING=:replace`` names a handler and no encoding, so the stream is still UTF-8."""
    monkeypatch.setenv("PYTHONIOENCODING", ":replace")
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="latin-1", errors="strict", newline=""))

    code = main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert code == 0
    assert _hostname_in(buffer.getvalue()) == HOSTNAME


def test_the_re_encoded_stream_keeps_its_error_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """Naming only an encoding resets the handler to ``strict``; the one the stream had is kept."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    stream = _RecordingErrors(io.BytesIO(), encoding="latin-1", errors="backslashreplace")
    monkeypatch.setattr(sys, "stdout", stream)

    code = main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert code == 0
    assert stream.seen, "nothing was written, so the handler in force was never observed"
    assert set(stream.seen) == {("utf-8", "backslashreplace")}, stream.seen


def test_a_stream_that_cannot_say_whether_it_is_a_terminal_keeps_its_encoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], AppServices],
) -> None:
    """Not knowing is treated as a terminal: the stream writes as it always did, never re-encoded."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", _CannotSay(buffer, encoding="latin-1", errors="strict", newline=""))

    main(_disks_as_json(tmp_path), services_factory=production_factory)

    assert b'"gr\xfc\xdfe-?"' in buffer.getvalue()
