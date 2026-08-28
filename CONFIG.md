# Configuration System

This project uses [`lib_layered_config`](https://github.com/bitranox/lib_layered_config) to manage configuration through a layered merging system. Configuration values are loaded from multiple sources and merged in a defined order, allowing flexible overrides from system-wide defaults down to individual command-line arguments.

## Key Concepts

- **Layered merging**: Configuration is assembled from multiple files and sources, with later layers overriding earlier ones
- **Cross-platform paths**: Follows XDG conventions on Linux, standard locations on macOS and Windows
- **Profile support**: Named profiles allow environment-specific configurations (e.g., `production`, `staging`, `test`)
- **TOML format**: All configuration files use TOML syntax
- **Runtime overrides**: Values can be overridden via environment variables or CLI flags without modifying files

---

## Configuration Layers

Configuration is loaded and merged in the following order (lowest to highest precedence):

| Priority | Layer        | Description                                     |
|:--------:|--------------|-------------------------------------------------|
| 1        | **defaults** | Bundled with the package (`defaultconfig.toml`) |
| 2        | **app**      | System-wide settings for all machines           |
| 3        | **host**     | Machine-specific overrides                      |
| 4        | **user**     | User's personal settings                        |
| 5        | **.env**     | Project directory dotenv file                   |
| 6        | **env vars** | Environment variables                           |
| 7        | **CLI**      | Command-line `--set` flags (highest priority)   |

**Merge behavior**: Each layer only needs to specify values it wants to override. Unspecified values inherit from lower layers.

---

## File Locations

### Platform-Specific Paths

| Layer    | Linux                                   | macOS                                                               | Windows                                               |
|----------|-----------------------------------------|---------------------------------------------------------------------|-------------------------------------------------------|
| defaults | (bundled with package)                  | (bundled with package)                                              | (bundled with package)                                |
| app      | `/etc/xdg/{slug}/config.toml`           | `/Library/Application Support/{vendor}/{app}/config.toml`           | `C:\ProgramData\{vendor}\{app}\config.toml`           |
| host     | `/etc/xdg/{slug}/hosts/{hostname}.toml` | `/Library/Application Support/{vendor}/{app}/hosts/{hostname}.toml` | `C:\ProgramData\{vendor}\{app}\hosts\{hostname}.toml` |
| user     | `~/.config/{slug}/config.toml`          | `~/Library/Application Support/{vendor}/{app}/config.toml`          | `%APPDATA%\{vendor}\{app}\config.toml`                |

### Path Placeholders

| Placeholder  | Linux           | macOS / Windows |
|--------------|-----------------|-----------------|
| `{slug}`     | `lsdsk`         | -               |
| `{vendor}`   | -               | `bitranox`      |
| `{app}`      | -               | `lsdsk`         |
| `{hostname}` | System hostname | System hostname |

### Concrete Examples

**Linux:**
- User config: `~/.config/lsdsk/config.toml`
- App config: `/etc/xdg/lsdsk/config.toml`
- Host config: `/etc/xdg/lsdsk/hosts/myserver.toml`

**macOS:**
- User config: `~/Library/Application Support/bitranox/lsdsk/config.toml`

**Windows:**
- User config: `%APPDATA%\bitranox\lsdsk\config.toml`

---

## CLI Commands

### Global Options

These options apply to all commands and go **before** the command name:

| Option                    | Description                                                               |
|---------------------------|---------------------------------------------------------------------------|
| `--version`               | Show version and exit.                                                    |
| `--profile NAME`          | Load configuration from a named profile (e.g., `production`, `test`).     |
| `--set SECTION.KEY=VALUE` | Override a configuration setting. Can be repeated for multiple overrides. |
| `--env-file PATH`         | Explicit `.env` file path. Skips the default upward directory search.     |
| `--replay FILE`           | Render a capture taken earlier instead of reading this machine.           |
| `--history-file FILE`     | Read and write counter history there rather than the per-user state file. |
| `--no-record`             | Judge counters against recorded history without adding this reading.      |
| `--expand-virtual`        | List every kernel-virtual device rather than tallying them in one line.   |
| `--traceback`             | Show full Python traceback on errors (useful for debugging).              |
| `--no-traceback`          | Hide traceback, show only error message (default).                        |

`--replay` and `--expand-virtual` are also accepted **after** a command that
honours them, and the command's own value wins. `--expand-virtual` is there
because the line tallying the folded-away devices names it, and a reader who
types what they were just told must not meet "no such option".

**Example usage:**

```bash
# Use a specific profile
lsdsk --profile production config

# Override settings at runtime (repeatable)
lsdsk --set lib_log_rich.console_level=DEBUG config

# Load configuration from an explicit .env file
lsdsk --env-file /etc/myapp/.env config

# Show full traceback for debugging
lsdsk --traceback config-deploy --target user
```

---

### View Configuration

Display the merged configuration from all sources (defaults -> app -> host -> user -> .env -> env vars).

#### Options Reference

| Option           | Required | Description                                                     |
|------------------|:--------:|-----------------------------------------------------------------|
| `--format`       | No       | Output format: `human` (default) or `json`.                     |
| `--section NAME` | No       | Show only a specific section (e.g., `lib_log_rich`, `logging`). |
| `--profile NAME` | No       | Load configuration for a specific profile.                      |

#### Examples

```bash
# Show merged configuration from all sources
lsdsk config

# Output as JSON (useful for scripting)
lsdsk config --format json

# Show specific section only
lsdsk config --section lib_log_rich

# Load configuration for a specific profile
lsdsk config --profile production

# Combine options
lsdsk config --profile staging --format json --section lib_log_rich
```

### Deploy Configuration Files

Deploy bundled default configuration to platform-specific directories.

#### Options Reference

| Option             | Required | Description                                                                       |
|--------------------|:--------:|-----------------------------------------------------------------------------------|
| `--target`         | Yes      | Target layer: `app`, `host`, or `user`. Can be specified multiple times.          |
| `--force`          | No       | Overwrite existing configuration files. Without this, existing files are skipped. |
| `--profile NAME`   | No       | Deploy to a profile-specific subdirectory (e.g., `profile/production/`).          |
| `--permissions`    | No       | Enable Unix permission setting (default).                                         |
| `--no-permissions` | No       | Disable permission setting; use system umask instead.                             |
| `--dir-mode MODE`  | No       | Override directory permissions (octal: `750` or `0o750`).                         |
| `--file-mode MODE` | No       | Override file permissions (octal: `640` or `0o640`).                              |

#### Basic Examples

```bash
# Create user configuration file
lsdsk config-deploy --target user

# Deploy to system-wide location (requires privileges)
sudo lsdsk config-deploy --target app

# Deploy host-specific configuration
sudo lsdsk config-deploy --target host

# Deploy to multiple locations at once
lsdsk config-deploy --target user --target host

# Overwrite existing configuration
lsdsk config-deploy --target user --force

# Deploy to a specific profile directory
lsdsk config-deploy --target user --profile production

# Deploy production profile and overwrite if exists
lsdsk config-deploy --target user --profile production --force
```

#### Deploying for Other Users

To deploy user-level configuration for a different user account, use `sudo -u`:

```bash
# Deploy user config for 'serviceaccount' user
sudo -u serviceaccount lsdsk config-deploy --target user

# Deploy with a specific profile
sudo -u serviceaccount lsdsk config-deploy --target user --profile production

# The config will be created at that user's home directory:
# /home/serviceaccount/.config/lsdsk/config.toml
```

**Important notes:**

- Using `sudo` alone (without `-u`) deploys to root's home directory, not the target user's
- Always use `sudo -u <username>` when deploying for service accounts or other users
- Files are created with ownership of the target user (correct behavior)
- File permissions are set according to the `user` layer defaults (`0o700`/`0o600` = private)

**Common deployment scenarios:**

```bash
# System admin deploying app-wide config (all users)
sudo lsdsk config-deploy --target app

# System admin deploying for a service account
sudo -u myservice lsdsk config-deploy --target user

# System admin deploying host-specific config
sudo lsdsk config-deploy --target host

# Regular user deploying their own config (no sudo needed)
lsdsk config-deploy --target user
```

#### File Permissions (POSIX Only)

On Linux and macOS, `config-deploy` sets Unix file permissions based on the target layer. Windows uses ACLs and ignores these settings.

| Target | Directory Mode      | File Mode           | Description                             |
|--------|:-------------------:|:-------------------:|-----------------------------------------|
| `app`  | `0o755` (rwxr-xr-x) | `0o644` (rw-r--r--) | World-readable for system-wide config   |
| `host` | `0o755` (rwxr-xr-x) | `0o644` (rw-r--r--) | World-readable for host-specific config |
| `user` | `0o700` (rwx------) | `0o600` (rw-------) | Private to user only                    |

**Permission options:**

```bash
# Skip permission setting entirely (use system umask)
lsdsk config-deploy --target user --no-permissions

# Override directory mode (octal)
lsdsk config-deploy --target user --dir-mode 750

# Override file mode (octal)
lsdsk config-deploy --target user --file-mode 640

# Both overrides together
lsdsk config-deploy --target user --dir-mode 750 --file-mode 640

# Octal formats: both "750" and "0o750" are accepted
lsdsk config-deploy --target user --dir-mode 0o750
```

**Configurable defaults:**

Permission defaults can be customized in `[lib_layered_config.default_permissions]`:

```toml
[lib_layered_config.default_permissions]
# Values: octal strings ("0o755", "755") or decimal integers (493)
app_directory = "0o755"
app_file = "0o644"
host_directory = "0o755"
host_file = "0o644"
user_directory = "0o700"
user_file = "0o600"

# Set to false to disable permission setting by default
enabled = true
```

### Generate Example Configuration Files

Create example TOML files showing all available options with default values and documentation comments. Useful for learning the configuration structure or creating initial configuration files.

#### Options Reference

| Option              | Required | Description                                                         |
|---------------------|:--------:|---------------------------------------------------------------------|
| `--destination DIR` | Yes      | Directory to write example files.                                   |
| `--force`           | No       | Overwrite existing files. Without this, existing files are skipped. |

#### Examples

```bash
# Generate examples in a specific directory
lsdsk config-generate-examples --destination ./examples

# Overwrite existing example files
lsdsk config-generate-examples --destination ./examples --force

# Generate examples in current directory
lsdsk config-generate-examples --destination .
```

#### Generated Files

| File              | Description                                                 |
|-------------------|-------------------------------------------------------------|
| `config.toml`     | Main configuration file with all sections                   |
| `config.d/*.toml` | Modular configuration files (logging, layered-config, etc.) |

Each file contains commented documentation explaining available options and their default values.

### Runtime Overrides

Use `--set` to override configuration values without modifying files. This option:
- Has the **highest precedence** (overrides all other sources including environment variables)
- Can be **repeated** to set multiple values
- Must appear **before** the command name

#### Syntax

```
--set SECTION.KEY=VALUE
--set SECTION.SUBSECTION.KEY=VALUE
```

#### Examples

```bash
# Override a single value
lsdsk --set lib_log_rich.console_level=DEBUG config

# Override multiple values
lsdsk --set lib_log_rich.console_level=DEBUG --set lib_log_rich.console_format_preset=short config

# Override nested values
lsdsk --set lib_log_rich.console_level=DEBUG config

# Override with JSON arrays/objects (use single quotes around the value)
lsdsk --set lib_log_rich.queue_enabled=false config

# Combine with profile
lsdsk --profile production --set lib_log_rich.console_level=DEBUG config
```

#### Supported Value Types

| Type        | Example                                                       |
|-------------|---------------------------------------------------------------|
| String      | `--set section.key=value`                                     |
| Integer     | `--set section.timeout=30`                                    |
| Float       | `--set section.ratio=0.5`                                     |
| Boolean     | `--set section.enabled=true` or `--set section.enabled=false` |
| JSON Array  | `--set section.hosts='["a.com", "b.com"]'`                    |
| JSON Object | `--set section.metadata='{"key": "value"}'`                   |

---

## What lsdsk Itself Reads

Three sections belong to lsdsk rather than to the configuration library. Every
value the tool judges or lays out by is one of these keys, so a threshold is
never a constant buried in a function.

Deploy them with `lsdsk config-deploy --target user`, which writes
`config.d/60-thresholds.toml`, `70-display.toml` and `50-history.toml` with the
shipped values and their explanations. Override one for a single run with
`--set`:

```bash
lsdsk --set thresholds.crc_errors_significant=10 findings
```

### `[thresholds]` - what the rules judge against

These decide severity, and therefore the exit code.

| Key                          | Default | Effect                                                                            |
|------------------------------|---------|-----------------------------------------------------------------------------------|
| `wear_warning_percent`       | `80`    | Rated endurance consumed before a drive is called a warning                       |
| `wear_critical_percent`      | `95`    | And before it is called critical                                                  |
| `crc_errors_significant`     | `100`   | Below this an interface CRC count is a hint rather than a warning                 |
| `mixed_firmware_threshold`   | `2`     | Distinct firmware revisions of one model before it is reported                    |
| `wear_projection_min_points` | `2`     | Recorded readings needed before a wear rate is projected to 100%                  |
| `quiet_expected_min`         | `10.0`  | Errors the drive's own rate must have predicted before silence counts as evidence |
| `min_span_hours`             | `1`     | Power-on hours between two readings before a rate is computed at all              |

### `[display]` - layout and colour

These change what a report looks like. None of them changes severity or the exit
code: that comes from `[thresholds]` and from the limits a drive publishes about
itself.

| Key                       | Default | Effect                                                 |
|---------------------------|---------|--------------------------------------------------------|
| `piped_width`             | `120`   | Width used when output is not a terminal               |
| `summary_limit`           | `6`     | Findings named in the verdict line before "and N more" |
| `wear_row_floor_percent`  | `10`    | Wear below this is not given a row in `lsdsk trend`    |
| `expand_virtual`          | `false` | List kernel-virtual devices instead of tallying them   |
| `wwn_width`               | `24`    | Most characters the wwn column is given in either view |
| `traceback_summary_limit` | `500`   | Characters kept in a short traceback                   |
| `traceback_verbose_limit` | `10000` | And under `--traceback`                                |

A kernel-virtual device is one the kernel provides with no hardware behind it:
zram, a loop mount, a ZFS zvol, a device-mapper node. It has no controller, no
link and no counters, so it is counted and named rather than listed. It is never
hidden: the header counts them, the tree and the disk table say how many were
left out, and the JSON envelope carries every one of them under `virtual_disks`
whatever this key is set to.

### `[history]` - the counter store

| Key                     | Default | Effect                                                                            |
|-------------------------|---------|-----------------------------------------------------------------------------------|
| `enabled`               | `true`  | Whether an ordinary run records a reading. `--no-record` turns it off for one run |
| `path`                  | `""`    | Where the store lives. Empty resolves per platform AND per privilege              |
| `max_samples_per_drive` | `512`   | Readings kept per drive before the series is thinned                              |

An empty `path` resolves to `/var/lib/lsdsk/history.json` for a root run on
Linux or macOS, and to the per-user state directory otherwise
(`$XDG_STATE_HOME/lsdsk/`, `~/Library/Application Support/bitranox/lsdsk/`,
`%LOCALAPPDATA%\bitranox\lsdsk\`). Reading the counters needs root, so on a
server the root path is the one that fills. `lsdsk record --format json` prints
the path this run resolved, under `store`.

Override it for one run with the global `--history-file`.

## Profiles

Profiles provide isolated configuration namespaces for different environments (e.g., `production`, `staging`, `test`).

### Profile Name Requirements

Profile names are validated for security and cross-platform compatibility:

| Rule                   | Description                                                                               |
|------------------------|-------------------------------------------------------------------------------------------|
| **Maximum length**     | 64 characters                                                                             |
| **Allowed characters** | ASCII letters (`a-z`, `A-Z`), digits (`0-9`), hyphens (`-`), underscores (`_`)            |
| **Start character**    | Must start with a letter or digit (not `-` or `_`)                                        |
| **Reserved names**     | Windows reserved names rejected: `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` |
| **Path safety**        | No path separators (`/`, `\`) or traversal sequences (`..`)                               |

**Valid examples:** `production`, `staging-v2`, `test_env`, `dev01`

**Invalid examples:** `../etc` (path traversal), `-invalid` (starts with hyphen), `CON` (Windows reserved)

### Which Layers Are Affected?

| Layer    | Affected by Profile? | Notes                               |
|----------|:--------------------:|-------------------------------------|
| defaults | No                   | Always loaded from package          |
| app      | Yes                  | Uses `profile/<name>/` subdirectory |
| host     | Yes                  | Uses `profile/<name>/` subdirectory |
| user     | Yes                  | Uses `profile/<name>/` subdirectory |
| .env     | No                   | Project directory                   |
| env vars | No                   | Environment                         |
| CLI      | No                   | Command line                        |

### Profile Path Examples

**Without profile:**
- `~/.config/lsdsk/config.toml`

**With profile `production`:**
- `~/.config/lsdsk/profile/production/config.toml`

### Reading Behavior

Profile directories are **separate namespaces**. Configuration deployed with a profile is only visible when reading with that same profile.

| Command                       | Sees `app` layer?                  | Sees `user` layer?                 |
|-------------------------------|------------------------------------|------------------------------------|
| `config` (no profile)         | Only if deployed without profile   | Only if deployed without profile   |
| `config --profile production` | Only if deployed with `production` | Only if deployed with `production` |

**Example**: If you deploy `app` with `--profile production` but `user` without a profile:

| Command                       | app layer | user layer |
|-------------------------------|:---------:|:----------:|
| `config`                      | No        | Yes        |
| `config --profile production` | Yes       | No         |

---

## Environment Variables

Configuration can be overridden via environment variables using two methods:

### Method 1: Native lib_log_rich Variables

For logging configuration, use the native `LOG_*` variables (highest precedence):

```bash
LOG_CONSOLE_LEVEL=DEBUG lsdsk info
LOG_ENABLE_GRAYLOG=true LOG_GRAYLOG_ENDPOINT="logs.example.com:12201" lsdsk info
```

### Method 2: Application-Prefixed Variables

For any configuration section, use the format: `<PREFIX>___<SECTION>__<KEY>=value`

```bash
LSDSK___LIB_LOG_RICH__CONSOLE_LEVEL=DEBUG lsdsk info
```

**Separator reference:**
- `___` (triple underscore) - separates prefix from section
- `__` (double underscore) - separates section from key

---

## .env File Support

Create a `.env` file in your project directory for local development overrides:

```bash
# .env
LOG_CONSOLE_LEVEL=DEBUG
LOG_CONSOLE_FORMAT_PRESET=short
LOG_ENABLE_GRAYLOG=false
```

By default, the application searches upward from the current directory to discover `.env` files.

To load a specific `.env` file instead, use `--env-file`:

```bash
# Load from an explicit path (skips upward directory search)
lsdsk --env-file /opt/myapp/config/.env config
```

The file must exist and be readable; Click validates this before the command runs.

---

## Default Configuration

The `defaultconfig.toml` and files in `defaultconfig.d/` (bundled with the package) provide baseline values. These serve as the fallback when no external configuration files are deployed.

---

## Customization Best Practices

**Do NOT modify deployed configuration files directly.** These files may be overwritten during package updates.

Instead, create your own override files in the appropriate layer directory using a high-numbered prefix:

```bash
# User-level customization (Linux)
~/.config/lsdsk/999-myconfig.toml

# User-level customization (macOS)
~/Library/Application Support/bitranox/lsdsk/999-myconfig.toml

# User-level customization (Windows)
%APPDATA%\bitranox\lsdsk\999-myconfig.toml

# System-wide customization (Linux)
/etc/xdg/lsdsk/999-myconfig.toml
```

**Why this works:**
- Files in each layer directory are loaded in alphabetical order
- Higher-numbered files (e.g., `999-`) load last and override earlier values
- Your custom file won't be touched by updates that regenerate `config.toml`

**Example `999-myconfig.toml`:**

```toml
# My custom overrides - survives package updates

[lib_log_rich]
console_level = "DEBUG"

