# Module Reference: architecture and file index

## Status

Current, and checked rather than asserted. `tests/test_module_reference.py` fails when a
module exists that is not listed here, or a listed path does not exist.

## Related files

### Domain Layer

- `src/lsdsk/domain/diagnostics.py` - Pure rules that turn an inventory into findings.
- `src/lsdsk/domain/enums.py` - Type-safe domain enums for output formats and deployment targets.
- `src/lsdsk/domain/errors.py` - Domain-specific exceptions for typed error handling at boundaries.
- `src/lsdsk/domain/history.py` - Counter history, and the rules that turn a stored total into a rate.
- `src/lsdsk/domain/models.py` - Typed value objects describing storage topology, health and diagnostics.
- `src/lsdsk/domain/thresholds.py` - Every number the rules judge by, in one place and overridable.

### Application Layer

- `src/lsdsk/application/ports.py` - Application ports - callable Protocol definitions for adapter functions.

### Adapters - hardware decode (pure)

- `src/lsdsk/adapters/hw/decode/ahci.py` - Decode the AHCI host controller capability registers.
- `src/lsdsk/adapters/hw/decode/ata_identify.py` - Decode the 512-byte ATA IDENTIFY DEVICE response.
- `src/lsdsk/adapters/hw/decode/ata_smart.py` - Decode the 512-byte ATA SMART READ DATA and THRESHOLDS structures.
- `src/lsdsk/adapters/hw/decode/nvme.py` - Decode NVMe Identify Controller and the SMART/Health log page.
- `src/lsdsk/adapters/hw/decode/pciids.py` - Resolve numeric PCI vendor and device identifiers to readable names.
- `src/lsdsk/adapters/hw/decode/text.py` - Strip control characters from a string the hardware chose.
- `src/lsdsk/adapters/hw/decode/virtualization.py` - Decide whether this machine is bare metal, a virtual machine, or a container.

### Adapters - hardware, Linux

- `src/lsdsk/adapters/hw/linux/builder.py` - Turn a captured Linux sysfs reading into the domain inventory.
- `src/lsdsk/adapters/hw/linux/reader.py` - Read storage topology and device blobs from a live Linux system.

### Adapters - hardware, Windows

- `src/lsdsk/adapters/hw/windows/builder.py` - Turn a captured Windows reading into the domain inventory.
- `src/lsdsk/adapters/hw/windows/reader.py` - Read storage topology and device blobs from a live Windows system.
- `src/lsdsk/adapters/hw/windows/winapi.py` - Win32 structures and bindings used to read storage hardware.

### Adapters - hardware, shared

- `src/lsdsk/adapters/hw/snapshot.py` - Capture a machine's storage subsystem to JSON, and replay it.

### Adapters - rendering

- `src/lsdsk/adapters/render/full.py` - The whole machine on one page.
- `src/lsdsk/adapters/render/layout.py` - Column layout for the topology tree.
- `src/lsdsk/adapters/render/report.py` - The default view: a problem summary above an aligned topology tree.
- `src/lsdsk/adapters/render/rows.py` - The row shape every table renderer builds.
- `src/lsdsk/adapters/render/tables.py` - Focused tables for the controller, disk and health views.
- `src/lsdsk/adapters/render/theme.py` - Formatting and colour vocabulary shared by every view.
- `src/lsdsk/adapters/render/trend.py` - Render what each watched counter is doing over time.

### Adapters - interactive

- `src/lsdsk/adapters/tui/app.py` - The interactive view: one page per question, over a single scan.
- `src/lsdsk/adapters/tui/typed_table.py` - A typed view of the table operations this app uses.

### Adapters - CLI

- `src/lsdsk/adapters/cli/constants.py` - Shared CLI constants.
- `src/lsdsk/adapters/cli/context.py` - Click context helpers for CLI state management.
- `src/lsdsk/adapters/cli/envelope.py` - The machine-readable envelope for commands that act rather than report.
- `src/lsdsk/adapters/cli/exit_codes.py` - POSIX-conventional exit codes for CLI error paths.
- `src/lsdsk/adapters/cli/main.py` - CLI entry point and execution wrapper.
- `src/lsdsk/adapters/cli/root.py` - Root CLI command group and global option handling.
- `src/lsdsk/adapters/cli/safe_console.py` - Encode-safe console output.
- `src/lsdsk/adapters/cli/typed_click.py` - Strictly-typed wrappers for rich_click's partially-typed decorators.

### Adapters - CLI commands

- `src/lsdsk/adapters/cli/commands/config.py` - Configuration display and deployment CLI commands.
- `src/lsdsk/adapters/cli/commands/history.py` - The commands and the sampling policy for counter history.
- `src/lsdsk/adapters/cli/commands/info.py` - Basic CLI commands: resolved metadata, and a deliberate failure testing.
- `src/lsdsk/adapters/cli/commands/logging.py` - Logging demonstration CLI command.
- `src/lsdsk/adapters/cli/commands/scan.py` - The commands that look at storage: the report, the tables and the snapshot.

### Adapters - configuration

- `src/lsdsk/adapters/config/deploy.py` - Deploy default configuration to app/host/user target directories.
- `src/lsdsk/adapters/config/display.py` - Display configuration - delegates to lib_layered_config.
- `src/lsdsk/adapters/config/history.py` - The ``[history]`` configuration section, parsed into a typed model.
- `src/lsdsk/adapters/config/loader.py` - Configuration loader with caching and profile/override support.
- `src/lsdsk/adapters/config/overrides.py` - Parse and apply ``--set SECTION.KEY=VALUE`` CLI overrides to Config.
- `src/lsdsk/adapters/config/permissions.py` - Permission settings loader for config deployment.
- `src/lsdsk/adapters/config/secrets.py` - A second, broader redaction pass over anything about to be printed.
- `src/lsdsk/adapters/config/tunables.py` - The `[thresholds]` and `[display]` sections, parsed into typed models.

### Adapters - counter history

- `src/lsdsk/adapters/history/store.py` - Where counter history is kept on disk, and what keeps that file trustworthy.

### Adapters - shared

- `src/lsdsk/adapters/textfile.py` - Reading a JSON file that came from somewhere else, without trusting its size.

### Adapters - logging

- `src/lsdsk/adapters/logging/setup.py` - Centralized logging initialization for all entry points.

### Adapters - in-memory, for tests

- `src/lsdsk/adapters/memory/config.py` - In-memory configuration adapters for testing.
- `src/lsdsk/adapters/memory/history.py` - In-memory counter history, for tests and for the testing composition root.
- `src/lsdsk/adapters/memory/info.py` - An in-memory stand-in for printing the package's own metadata.
- `src/lsdsk/adapters/memory/logging.py` - In-memory logging adapter for testing.

### Composition

- `src/lsdsk/composition/__init__.py` - Composition root wiring adapters to application ports.

### Entry points

- `src/lsdsk/entry.py` - Console script entry point with production wiring.
- `src/lsdsk/__main__.py` - Module entry point for ``python -m lsdsk``.
- `src/lsdsk/__init__.py` - Public package surface: configuration and package metadata.
- `src/lsdsk/__init__conf__.py` - Static package metadata surfaced to CLI commands and documentation.

## Architecture

The layer rule is enforced by import-linter contracts in `pyproject.toml`, not by convention:
`domain` imports nothing from `adapters` or `application`, and the layers run
domain -> application -> adapters -> composition. Run `lint-imports` to check.

Two splits inside the adapters carry most of the design:

- **Decoding is separate from transport.** Linux and Windows obtain the same binary structures
  over different transports, so `adapters/hw/decode/` is pure `bytes` in, domain values out, and
  is exercised on every CI runner regardless of platform.
- **Each platform splits again into `reader` and `builder`.** The reader is impure and touches
  sysfs or Win32; the builder is pure and turns a reading into domain objects. That is why the
  Linux mapping is tested on Windows and the Windows mapping on Linux, and why `--replay` renders
  exactly what a live run would.

The domain layer is frozen dataclasses rather than Pydantic, because it has no serialization
concern. Pydantic sits at the two boundaries that do: `CaptureEnvelope` validating a replayed
snapshot on the way in, and `ScanEnvelope` producing the JSON on the way out.

## CLI commands

Seven of these are sections of the default report, which a bare `lsdsk` prints in full:
`topology`, `controllers`, `disks`, `health`, `smart`, `findings` and `slots`. The rest
stand alone. Every command that produces data takes `--format json`; `tui`, `fail` and
`logdemo` do not, having none to structure.

| Command                    | Purpose                                                               |
|----------------------------|-----------------------------------------------------------------------|
| `config`                   | Display the current merged configuration from all sources.            |
| `config-deploy`            | Deploy default configuration to system or user directories.           |
| `config-generate-examples` | Generate example configuration files in a target directory.           |
| `controllers`              | List storage controllers, their PCIe placement and their free ports.  |
| `disks`                    | List every disk with its identity and its interface speed.            |
| `fail`                     | Trigger the intentional failure helper to test error handling.        |
| `findings`                 | Explain every problem and improvement in full.                        |
| `health`                   | Show wear, temperature, hours and error counters for every disk.      |
| `info`                     | Print resolved metadata so users can inspect installation details.    |
| `logdemo`                  | Run a logging demonstration to preview log output.                    |
| `record`                   | Record this machine's error counters and print nothing.               |
| `slots`                    | Show the mainboard's PCIe ports, what occupies them and what is free. |
| `smart`                    | Show every disk's SMART attributes against its own thresholds.        |
| `snapshot`                 | Capture this machine's raw reading for replay elsewhere.              |
| `topology`                 | Show the problem summary and the disk-to-controller tree.             |
| `trend`                    | Show what each error counter is doing over time, not just its total.  |
| `tui`                      | Open the interactive view, with a page per question.                  |

## Exit codes

A scan command answers a question, so its code says what the answer was: `0` nothing
actionable, `1` a warning or a critical. A hint never sets a non-zero code, because it
describes a ceiling rather than a fault. Anything above `1` means the command did not run.

| Code | Name                | Raised when                                                                          |
|------|---------------------|--------------------------------------------------------------------------------------|
| 0    | `SUCCESS`           | The command ran and found nothing actionable                                         |
| 1    | `GENERAL_ERROR`     | A scan found a warning or a critical, or an action failed                            |
| 13   | `PERMISSION_DENIED` | A deployment target or a device needs privilege this run does not have               |
| 22   | `INVALID_ARGUMENT`  | A named configuration section does not exist, or a `--profile` name was rejected     |
| 78   | `CONFIG_ERROR`      | A file is not a snapshot this version reads, or this platform has no hardware reader |

Two more a caller will see are not in the enum, because lsdsk does not raise them:

| Code            | Source  | Meaning                                                                                       |
|-----------------|---------|-----------------------------------------------------------------------------------------------|
| 2               | Click   | A usage error: an unknown option or command, a missing argument, a bad choice, an absent path |
| 130 / 141 / 143 | signals | Interrupt, broken pipe, terminate, translated by `lib_cli_exit_tools`                         |

`2` is worth stating carefully: it does NOT mean "the file was missing". Five different
usage mistakes produce it, and `tests/test_cli_exit_codes.py` drives all five.
