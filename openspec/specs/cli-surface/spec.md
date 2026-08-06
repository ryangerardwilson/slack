# cli-surface Specification

## Purpose

Define the top-level Slack CLI contract: global commands, preset-scoped grammar, help/version/upgrade behavior, exit codes, and output style. This is the agent- and human-facing surface of the `slack` binary.

## Requirements

### Requirement: Flat declarative command grammar
The CLI SHALL expose a flat declarative command grammar with numeric account presets and SHALL reject retired compressed command aliases.

#### Scenario: No-args and help show the same help text
- **WHEN** the user runs `slack` with no arguments or `slack help`
- **THEN** the process SHALL print the human-written help text to stdout
- **AND** the process SHALL exit 0

#### Scenario: Preset is required for account-scoped work
- **WHEN** an account-scoped command is invoked
- **THEN** the first argument MUST be a numeric preset string (e.g. `1`, `2`)
- **AND** there MUST NOT be a config-level default preset that fills the preset when omitted

#### Scenario: Retired short commands are rejected
- **WHEN** the user invokes a retired alias such as `cfg`, `conf`, `ac`, `post`, `dm`, `ls`, `mra`, `sc`, `su`, `u`, `o`, `df`, or bare `tui` as a top-level or second-token retired form
- **THEN** the CLI SHALL return a usage error directing the user to declarative commands and `slack help`
- **AND** the process SHALL exit 2 for usage errors

#### Scenario: Global launcher actions remain limited
- **WHEN** only global (non-preset) launcher actions are considered
- **THEN** the supported set SHALL include `help`, `version`, `upgrade`, `config`, `config edit`, `accounts list`, `setup check`, `auth` (and auth subforms), and `mark all read`
- **AND** `help`, `version`, and `upgrade` SHALL NOT accept extra arguments

#### Scenario: Reply is a current declarative command
- **WHEN** the user runs `slack <preset> reply to <message_id> body <message>`
- **THEN** the parser MUST accept the command (it is not a retired alias)
- **AND** preview reply and execution reply SHALL share the same target grammar

### Requirement: Version and upgrade
The CLI SHALL report a stamped runtime version and provide an in-place upgrade path via the public install script.

#### Scenario: Version prints stamped value
- **WHEN** the user runs `slack version`
- **THEN** stdout SHALL contain exactly the stamped version string (source checkouts default to `0.0.0` until release stamping)
- **AND** the process SHALL exit 0

#### Scenario: Upgrade invokes install script
- **WHEN** the user runs `slack upgrade`
- **THEN** the runtime SHALL run the public install script upgrade path (`install.sh` from the repository raw URL with `upgrade`)
- **AND** it SHALL replace the current process via bash when not under a test `RunCommand` hook

### Requirement: Help content stays compact and non-leaky
Help output SHALL remain human-written, compact, terminal-default styled, and free of config-path, token, and environment-variable inventories.

#### Scenario: Help omits secrets and paths
- **WHEN** `slack help` is printed
- **THEN** the text SHALL document the declarative command shapes used by agents and humans
- **AND** it MUST NOT enumerate absolute config paths, raw token formats beyond high-level command usage, or environment-variable inventories

### Requirement: Deterministic process exit codes
The CLI SHALL use stable exit codes for success, runtime failure, and usage errors.

#### Scenario: Success exits zero
- **WHEN** a command completes without error
- **THEN** the process exit code SHALL be 0

#### Scenario: Usage errors exit two
- **WHEN** argument parsing or command shape validation fails with a `UsageError`
- **THEN** the error message SHALL be written to stderr
- **AND** the process exit code SHALL be 2

#### Scenario: Runtime errors exit one
- **WHEN** a non-usage error occurs (API failure, I/O, missing token after valid shape)
- **THEN** the error message SHALL be written to stderr
- **AND** the process exit code SHALL be 1

### Requirement: Plain-text and optional JSON output
Command output SHALL be script-friendly plain text by default, with opt-in JSON on supported list/status commands via `output json`.

#### Scenario: Default plain sections
- **WHEN** a multi-row listing command runs without `output json`
- **THEN** rows SHALL be printed as blank-line-separated `key: value` sections

#### Scenario: JSON opt-in
- **WHEN** a supported command is invoked with trailing `output json` or `--json`
- **THEN** stdout SHALL be a single indented JSON document suitable for machine consumption
