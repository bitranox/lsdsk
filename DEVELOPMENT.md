# Developing lsdsk

## Setting up

```bash
uv sync                      # creates .venv with the dev extras
.venv/bin/lsdsk --version
```

Python 3.11 or newer. The project targets Linux and Windows; the test suite runs
on either, and the platform-specific readers are exercised off-platform through
committed captures.

## The build system

Everything runs through [bmk](https://github.com/bitranox/bmk), which GENERATES
the `Makefile`. The first line of `Makefile` reads `# BMK MAKEFILE <version>`,
and any bmk-invoking target rewrites it, so a modified `Makefile` in your working
tree after running `make test` is expected and safe to commit.

**`make help` is the authoritative list of targets.** It greps the Makefile's own
target comments, so it cannot disagree with what actually runs. This document
deliberately does not restate it: a hand-copied list goes stale the moment bmk
regenerates the file.

The ones you will use most:

| Command                | What it does                                                           |
|------------------------|------------------------------------------------------------------------|
| `make test`            | The gate: format, lint, type-check, import contracts, security, pytest |
| `make testintegration` | Only the tests marked `integration`, which need real machines          |
| `make test-all`        | The gate on every declared Python version                              |
| `make run`             | Run the CLI through bmk                                                |
| `make build`           | Build the wheel and sdist into `dist/`                                 |
| `make push`            | Run the gate, then commit and push                                     |
| `make release`         | Tag and publish                                                        |

Run `make help` for the rest, and `bmk <command> --help` for a command's own
options.

## Tests

```bash
make test                    # the gate, and what CI runs
.venv/bin/python -m pytest tests/ -q          # pytest alone, faster while iterating
.venv/bin/python -m pytest tests/ -q -k trend # one area
```

`make test` is the pre-push gate. It is a strict superset of any subset you might
run by hand, so a green `ruff` plus a green `pytest` is not the same signal.

### Markers

| Marker                                              | Meaning                                            |
|-----------------------------------------------------|----------------------------------------------------|
| `os_agnostic`                                       | Runs everywhere                                    |
| `os_posix` / `os_linux` / `os_macos` / `os_windows` | Skipped elsewhere, wired in `tests/conftest.py`    |
| `integration`                                       | Needs external machines; excluded from `make test` |
| `local_only`                                        | Needs this developer's fleet; never runs in CI     |

A marker only skips because `tests/conftest.py` wires it. Registering one in
`pyproject.toml` silences the unknown-marker warning and skips nothing.

### Real-hardware tests

`tests/e2e/` ships a probe to real machines over ssh and asserts the contract
there. A replayed capture proves the decoders and the builders; it cannot prove
the platform reader, because a capture already holds whatever the reader
managed to get.

They need a fleet, so the host list lives in `.env`, which is never committed:

```bash
LSDSK_E2E_HOSTS="server-a:root:linux,workstation-b:admin:windows"
```

See `.env.example` for the format. Unset, the tests skip and say so. Each run
reports what it touched, both in the terminal summary and in `e2e-coverage.json`,
because `make testintegration` prints only bmk's own result envelope and a sweep
of no machines would otherwise look identical to a full one.

### Fixtures

`tests/fixtures/hw/*.json` are captures from real machines, produced by the
production reader, with the drive serial numbers and the machine names replaced
by synthetic ones so the repository does not publish which physical units these
are. Models, firmware revisions and every measurement are as captured.

Each is named for what it holds rather than where it came from, so a test
naming one says what it is testing: `linux-sas-hba`, `linux-nvme-board`,
`linux-minimal`, `windows-ahci`. `linux-sas-hba-later` is the same capture
taken later, and the two deliberately share a hostname because the history
tests need it - the store refuses to mix two machines.

Refresh or add one with:

```bash
lsdsk snapshot -o /tmp/capture.json
```

then rename it, rewrite its `hostname` field, and replace the serials before
committing. The serial appears three times in a capture - in the JSON field,
in the ATA IDENTIFY or NVMe Identify blob, and in the SCSI VPD page 0x89 that
the unprivileged Linux path reads - so scrubbing only the JSON field leaves the
real one embedded and makes the fixture disagree with itself.

Assert on bounded ranges rather than exact values for anything that changes on
its own: power-on hours and wear only climb, and an exact assertion breaks the
next time a fixture is recaptured.

## Code quality

Layer boundaries are enforced by import-linter contracts in `pyproject.toml`;
`lint-imports` checks them. Typing is pyright strict, and third-party typing gaps
are closed with typed facades rather than suppressions: see
`adapters/cli/typed_click.py` and `adapters/tui/typed_table.py`.

`make test` runs `pip-audit`. A finding is addressed by raising the floor of the
affected dependency in `pyproject.toml`, in `[project].dependencies` or in the
dev extra depending on where it lives, with the CVE noted inline.

## CI and releasing

Workflows live in `.github/workflows/` and are managed by an external template.
**Do not edit them in this repository**; change them in the template and
redistribute.

| Workflow                     | When                  |
|------------------------------|-----------------------|
| `default_cicd_public.yml`    | Push and pull request |
| `default_release_public.yml` | Publishing a release  |
| `codeql.yml`                 | Weekly, plus pushes   |

Releasing goes through bmk (`make release` / `make ship`), which bumps the
version, tags `vX.Y.Z` and publishes. Publishing authenticates either with a
`PYPI_API_TOKEN` secret or, when that secret is absent, through a PyPI Trusted
Publisher using the workflow's OIDC identity.

The version lives in `pyproject.toml` and is mirrored in `__init__conf__.py` and
`.claude-plugin/plugin.json`; `tests/test_metadata_sync.py` fails when they
disagree.

## This repository is also a Claude Code marketplace

It ships the skill at `skills/lsdsk/`. Bump `version` in
`.claude-plugin/plugin.json` on every shipped change, or existing installs never
re-fetch. `skills/lsdsk/SKILL.md` is edited only through the
`bitranox:meta-skill-writer` procedure; a guard enforces it.
