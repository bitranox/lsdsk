# Security Policy

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
