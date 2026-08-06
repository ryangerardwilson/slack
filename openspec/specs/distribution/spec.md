# distribution Specification

## Purpose

Define installation, upgrade, version stamping, and local development install paths for the `slack` binary.

## Requirements

### Requirement: Public installer script
`install.sh` SHALL install or upgrade the Linux x64 release binary and expose a user-local launcher.

#### Scenario: Default install
- **WHEN** the user runs `install.sh` with no arguments
- **THEN** the script SHALL install the latest GitHub release artifact into `~/.slack/bin`
- **AND** write or refresh a managed launcher at `~/.local/bin/slack`

#### Scenario: Installer help and version
- **WHEN** the user runs `install.sh help`
- **THEN** usage for install, version, upgrade, and `from <path>` SHALL be printed
- **WHEN** the user runs `install.sh version`
- **THEN** the latest release version tag SHALL be printed
- **WHEN** the user runs `install.sh version <ver>`
- **THEN** that specific release version SHALL be installed

#### Scenario: Upgrade when newer
- **WHEN** the user runs `install.sh upgrade`
- **THEN** the script SHALL compare the installed version to the latest release and upgrade when a newer release exists

### Requirement: Local checkout install
Developers SHALL be able to install from a local binary or source checkout without a GitHub release.

#### Scenario: From path
- **WHEN** the user runs `install.sh from <path>`
- **THEN** the script SHALL install from a local binary or build from a source checkout at that path into the managed install layout

### Requirement: Runtime version package
The Go binary SHALL report version from `internal/version.Version`.

#### Scenario: Source default
- **WHEN** the binary is built from an unstamped source tree
- **THEN** `slack version` SHALL print `0.0.0`

#### Scenario: Release stamp
- **WHEN** release automation builds an artifact
- **THEN** it MAY overwrite `internal/version.Version` (or equivalent ldflags) so installed binaries report the release tag

### Requirement: In-app upgrade
`slack upgrade` SHALL reuse the public install script upgrade path.

#### Scenario: Upgrade command
- **WHEN** the user runs `slack upgrade`
- **THEN** the process SHALL execute `curl -fsSL <raw install.sh URL> | bash -s -- upgrade` (or a test-injected `RunCommand` equivalent)
