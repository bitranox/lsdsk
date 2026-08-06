# Installing lsdsk

lsdsk is not published yet. Until the first release reaches PyPI, install it
from a checkout or from a wheel you build; everything under
[After the first release](#after-the-first-release) describes what will work
once it ships and does not work today.

Python 3.11 or newer, on Linux or Windows. Any other platform runs `--replay`
against a capture taken on one of those.

## From a checkout

```bash
git clone <repository-url> lsdsk
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
uv run --with ./lsdsk-<version>-py3-none-any.whl lsdsk topology
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

## After the first release

Once `v1.0.0` is published these will work, and none of them does yet:

```bash
uvx lsdsk topology                  # run without installing
uv tool install lsdsk               # install for everyday use
uv tool upgrade lsdsk
pipx install lsdsk                  # apt install pipx, if you prefer pipx
pip install lsdsk                   # inside a virtual environment
```

## Verifying an install

```bash
lsdsk --version                     # prints the version
lsdsk --help                        # lists every command
lsdsk topology                      # reads this machine
```

Installation registers one console script, `lsdsk`.

## Uninstalling

```bash
uv tool uninstall lsdsk             # or: pipx uninstall lsdsk / pip uninstall lsdsk
```

Configuration and the counter history are left behind. Remove them by hand if
you want them gone: `lsdsk config` prints where configuration was loaded from,
and `lsdsk record --format json` reports the history store's path under `store`.
