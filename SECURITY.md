# Security Policy

**English** | [Deutsch](de/SECURITY.md)

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities.
2. Email the maintainer at `bitranox@gmail.com`, the address published in the
   package metadata. GitHub's private vulnerability reporting is not enabled on
   this repository, so an advisory cannot be opened there.
3. Include a description of the vulnerability, steps to reproduce, and any
   relevant logs or screenshots.

You can expect an initial response within 72 hours.

## Subprocess and network surface

lsdsk starts no subprocess and makes no network request, with three narrow
exceptions:

- **The Graylog log sink** is off by default. When you enable it, it opens a
  connection to the Graylog host you configure, and does nothing else.
- **Installing uv on your consent.** If you accept the offer to create a desktop
  shortcut on a machine that has no `uvx`, lsdsk can install uv for you by
  running the official astral.sh installer. It runs only after you confirm, the
  installer is pinned to a known uv version, it is confined to the single module
  `adapters/uv_bootstrap.py`, and `tests/test_no_subprocess_or_network.py` fails
  the build if any other module shells out or opens a socket.
- **The elevation relaunch** starts lsdsk's own elevated copy: a UAC prompt on
  Windows, `sudo` on Linux and macOS. It starts no other program, and
  `--no-elevate` turns it off.

## Security Tooling

This project employs the following security measures in CI:

- **Bandit** -- static analysis for common Python security issues
- **CodeQL** -- GitHub code scanning (weekly schedule)
- **pip-audit** -- dependency vulnerability scanning against the PyPI advisory
  service, which is its default source
- **Ruff security rules** -- S (flake8-bandit) and B (flake8-bugbear) rulesets

## Dependency Policy

- Production dependencies use minimum version constraints (`>=`) and are audited
  on every CI run via `pip-audit`.
- Known CVEs in transitive dependencies are pinned in `pyproject.toml` with
  inline comments referencing the CVE identifier.
