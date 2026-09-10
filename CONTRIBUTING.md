# Contributing Guide

Thanks for helping improve **lsdsk**. The sections below summarise the day-to-day workflow, highlight the repository automation, and list the checks that must pass before a change is merged.

## 1. Workflow Overview

1. Fork and branch -- use short, imperative branch names (`feature/cli-extension`, `fix/codecov-token`).
2. Make focused commits -- keep unrelated refactors out of the same change.
3. Run `make test` locally before pushing (see the automation note below).
4. Update documentation and changelog entries that are affected by the change.
5. Open a pull request referencing any relevant issues.

## 2. Commits & Pushes

- Commit messages should be imperative (`Add rich handler`, `Fix CLI exit codes`).
- The test harness (`make test`) runs the full lint/type/test pipeline and does write to the tree: it regenerates the `Makefile`, applies Ruff's formatter and fixes, and raises dependency floors in `pyproject.toml`. Review those changes rather than assuming a clean run left nothing behind, and create commits yourself before pushing or uploading coverage artifacts.
- `make push` always performs a commit before pushing. It prompts for a message when run interactively, honours `MSG="..."` when provided (the Makefile forwards it as `BMK_COMMIT_MESSAGE`), and creates an empty commit if nothing is staged.

## 3. Coding Standards

- Apply the repository's Clean Architecture / SOLID rules
- Prefer small, single-purpose modules and functions; avoid mixing orthogonal concerns.
- Free functions and modules use `snake_case`; classes are `PascalCase`.
- Keep runtime dependencies minimal. Use the standard library where practical.

## 4. Tests & Tooling

- `make test` applies Ruff's formatter and fixes, then runs Bandit, import contracts (`lint-imports`), pip-audit, Pyright, Pytest, Ruff's format and lint checks, PSScriptAnalyzer and ShellCheck. Pytest always runs under coverage; there is no switch that turns it off.
- The harness syncs the project `.venv` before its gates, so the tools are installed for you. That sync also removes packages the declared extras do not pull, so do not keep anything in `.venv` that `pyproject.toml` does not name.
- Codecov uploads require a commit (provided by the automatic commit described above). For private repositories set `CODECOV_TOKEN` in your environment or `.env`.
- Tests follow a narrative style: prefer names like `test_when_<condition>_<outcome>()`, keep each case laser-focused, and mark OS constraints with the provided markers (`@pytest.mark.os_agnostic`, `@pytest.mark.os_windows`, etc.).
- Whenever you add a CLI behaviour or change metadata fallbacks, update the relevant story in the matching `tests/test_*.py` so the specification remains complete.

## 5. Documentation Checklist

Before opening a PR, confirm the following:

- [ ] `make test` passes locally, and you have reviewed the files it rewrote.
- [ ] Relevant documentation (`README.md`, `DEVELOPMENT.md`, `docs/systemdesign/*`) is updated.
- [ ] No generated artefacts or virtual environments are committed.
- [ ] Version bumps, when required, keep `pyproject.toml`, `CHANGELOG.md`, `src/lsdsk/__init__conf__.py` and `.claude-plugin/plugin.json` in step. `make bump-patch` does all four; `tests/test_metadata_sync.py` fails if one is left behind.

## 6. Security & Configuration

- Never commit secrets. Tokens (Codecov, PyPI) belong in `.env` (ignored by git) or CI secrets.
- Logging runs through `lib_log_rich`'s scrubber. Printed configuration gets its own redaction pass in `adapters/config/secrets.py`, which is not on the logging path; add a sensitive-key pattern to whichever of the two covers your case rather than redacting at the call site.

Happy hacking!
