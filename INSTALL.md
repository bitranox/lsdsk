# Installing lsdsk

**English** | [Deutsch](de/INSTALL.md)

Python 3.11 or newer, on Linux or Windows. Any other platform runs `--replay`
against a capture taken on one of those.

## Easiest: install and run with uv

`uv` fetches the right Python and the tool itself, so one line gets a working
`lsdsk` with nothing else set up first:

```bash
uvx lsdsk@latest                    # run it once, installing nothing
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

## Why we recommend uv: supply-chain protection

This repository commits a `uv.lock`. It pins every package the project needs, transitive
dependencies included, to an exact version and to the hashes of that version's files. The
`exclude-newer` setting under `[tool.uv]` in `pyproject.toml` adds a cooldown: uv leaves any
release younger than that out of the lock. CI installs from the lock, so a hijacked release that
gets pulled again within the cooldown never reaches our builds, and a file whose hash does not
match is refused.

That protection stays in this repository. The installable package declares only lower bounds
(`>=`) for its dependencies, so a plain `pip install` takes the newest release of every transitive
dependency at the moment you run it. If one of them was hijacked an hour earlier, you get the
hijacked version. `pipx install` and `uv tool install` without options do the same.

To install the command line tool with a cooldown of your own (uv then skips any release younger
than 7 days, transitive ones included), or to run the exact tree our CI tests:

```bash
uv tool install --exclude-newer "7 days" lsdsk
git clone https://github.com/bitranox/lsdsk.git && cd lsdsk && uv sync --locked && uv run lsdsk --help
```

If you depend on lsdsk in your own project, give that project the same cooldown (7 days here, as
an example) and commit the `uv.lock` that `uv add lsdsk` writes:

```toml
[tool.uv]
exclude-newer = "7 days"
```

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
gives a SATA controller its port speed and free-port count. On Linux before
kernel 6.9 a fifth does too: what a USB disk itself can do, which older kernels
publish only through usbfs. Without them the
affected columns read `-` and the header says so.

Reading the counters needs root, so a scheduled `lsdsk record` belongs in the
root crontab or a systemd timer rather than a user one.

## Verifying an install

```bash
lsdsk --version                     # prints the version
lsdsk --help                        # lists every command
lsdsk                               # reads this machine
```

## Desktop shortcut

lsdsk can create a desktop shortcut that launches it, so you need not type the
command each time. The interactive view offers one the first time you run it at
a terminal, and you can also ask for it outright:

```bash
lsdsk install-shortcut              # write the shortcut for this platform
lsdsk install-shortcut --install-uv # and install uv first if none is present
```

The shortcut goes into your desktop folder, and lsdsk never creates that folder:
on an account with none, such as a server with no graphical interface, the
interactive view offers no shortcut and `lsdsk install-shortcut` refuses, naming
the folder it looked for. On Linux that folder is the one your desktop records
in `~/.config/user-dirs.dirs` (`~/Schreibtisch` on a German desktop), and
`~/Desktop` where it records none.

On Linux it writes a `.desktop` entry (to the desktop and the application
menu), on macOS a `.command` file, and on Windows a `.lnk` shortcut (to the
desktop and the Start menu). The Windows shortcut starts lsdsk as Administrator:
Windows asks once through UAC before lsdsk starts, and the view opens in that
one window; cancelling the prompt starts nothing. On Windows the desktop is the
folder Windows names for your account, including one moved into OneDrive. Where
`uvx` is available the shortcut launches lsdsk through it, with the same 7-day
cooldown the project pins, so a start from the desktop gets the supply-chain
protection a plain `uvx` run would. (If only a plainly installed `lsdsk` is on
your PATH, it launches that instead.) The `uvx` launch looks like:

```bash
uvx --exclude-newer "7 days" --exclude-newer-package <owned-package>=false lsdsk@latest
```

with one `--exclude-newer-package <name>=false` for each package lsdsk owns in
its dependency closure, which exempts those from the cooldown so a new lsdsk
release is not held back by it.

If the machine has no uv, there is nothing for the shortcut to launch yet. To
install uv yourself (latest), run the official astral.sh installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                                       # Linux and macOS
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"    # Windows
```

With `--install-uv`, or when you accept the interactive offer, lsdsk installs uv
for you from that same official installer, only after you confirm. Its own
install pins a known uv version rather than fetching whatever is newest, so the
command lsdsk runs names a fixed release while the line above tracks the latest.
Set `display.offer_desktop_shortcut = false` to turn the interactive offer off
(see [CONFIG.md](https://github.com/bitranox/lsdsk/blob/main/CONFIG.md)).

## Uninstalling

```bash
uv tool uninstall lsdsk             # or: pipx uninstall lsdsk / pip uninstall lsdsk
```

Configuration and the counter history are left behind. Remove them by hand if
you want them gone: `lsdsk config` prints where configuration was loaded from,
and `lsdsk record --format json` reports the history store's path under `store`.
