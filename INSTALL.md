# Installing lsdsk

Python 3.11 or newer, on Linux or Windows. Any other platform runs `--replay`
against a capture taken on one of those.

## Easiest: install and run with uv

`uv` fetches the right Python and the tool itself, so one line gets a working
`lsdsk` with nothing else set up first:

```bash
uvx lsdsk                           # run it once, installing nothing
uv tool install lsdsk               # keep it on the PATH
```

If you do not have `uv` yet, its homepage carries the one-line installer for
Linux, macOS and Windows: <https://docs.astral.sh/uv/getting-started/installation/>
(project home: <https://docs.astral.sh/uv/>).

The rest of this page covers the other routes: pipx and pip, a checkout, and a
wheel on a machine that has neither.

## From PyPI without uv

```bash
pipx install lsdsk                  # isolated, on the PATH
pip install lsdsk                   # inside a virtual environment
lsdsk --version
```

`uv tool upgrade lsdsk` moves a uv install to the current release, and
`pipx upgrade lsdsk` does the same for a pipx one.

Installation registers one console script, `lsdsk`.

## From a checkout

For working on lsdsk itself, or to run a version that is not released.

```bash
git clone https://github.com/bitranox/lsdsk.git
cd lsdsk
uv sync                      # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/lsdsk --version
```

`uv sync` creates `.venv` and installs the project. `dev` is an extra rather
than a dependency group, so the test and lint tools come only with
`uv sync --extra dev`. The console script lands at `.venv/bin/lsdsk`
(`.venv\Scripts\lsdsk.exe` on Windows).

To put it on your PATH for everyday use:

```bash
uv tool install .            # from the checkout directory
lsdsk --version
```

## From a wheel

Useful for installing onto a machine that has no checkout, which is the normal
way to get it onto a server:

```bash
make build                   # writes dist/lsdsk-<version>-py3-none-any.whl
```

Then, on the target machine:

```bash
uv tool install ./lsdsk-<version>-py3-none-any.whl
# or, without installing anything permanently:
uv run --with ./lsdsk-<version>-py3-none-any.whl lsdsk
```

`pip install ./lsdsk-<version>-py3-none-any.whl` works the same way in a
virtual environment.

## What needs root

lsdsk runs unprivileged and says what that costs. Topology, link speeds,
capacity and firmware read without any privilege. Four things need root or
Administrator: SMART attributes and wear, the error counters that `trend` and
`record` use, PCIe connector detection, and the AHCI capability register that
gives a SATA controller its port speed and free-port count. Without them the
affected columns read `-` and the header says so.

Reading the counters needs root, so a scheduled `lsdsk record` belongs in the
root crontab or a systemd timer rather than a user one.

## Verifying an install

```bash
lsdsk --version                     # prints the version
lsdsk --help                        # lists every command
lsdsk                               # reads this machine
```

## Uninstalling

```bash
uv tool uninstall lsdsk             # or: pipx uninstall lsdsk / pip uninstall lsdsk
```

Configuration and the counter history are left behind. Remove them by hand if
you want them gone: `lsdsk config` prints where configuration was loaded from,
and `lsdsk record --format json` reports the history store's path under `store`.
