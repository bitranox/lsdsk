# Installing lsdsk

Python 3.11 or newer, on Linux or Windows. Any other platform runs `--replay`
against a capture taken on one of those.

## From PyPI

```bash
uvx lsdsk                           # run it once, installing nothing
uv tool install lsdsk               # install for everyday use
lsdsk --version
```

`uv tool upgrade lsdsk` moves it to the current release. If you prefer pipx,
`pipx install lsdsk`; inside a virtual environment, `pip install lsdsk`.

Installation registers one console script, `lsdsk`.

## From a checkout

For working on lsdsk itself, or to run a version that is not released.

```bash
git clone https://github.com/bitranox/lsdsk.git
cd lsdsk
uv sync                      # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/lsdsk --version
```

`uv sync` creates `.venv` and installs the project with its development extras.
The console script lands at `.venv/bin/lsdsk` (`\.venv\Scripts\lsdsk.exe` on
Windows).

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
