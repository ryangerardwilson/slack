# account-config Specification

## Purpose

Define how Slack account presets, tokens, contacts, and setup diagnostics are stored and managed. Configuration is the primary credential store for the CLI.

## Requirements

### Requirement: XDG config path and JSON shape
The application SHALL store configuration as plain JSON at the XDG config path for the `slack` app.

#### Scenario: Default config path
- **WHEN** no path override is supplied
- **THEN** the config file path SHALL be `$XDG_CONFIG_HOME/slack/config.json` if `XDG_CONFIG_HOME` is set
- **AND** otherwise SHALL be `~/.config/slack/config.json`

#### Scenario: Config file permissions on write
- **WHEN** the config is saved
- **THEN** the file SHALL be written with mode `0600`
- **AND** the parent directory SHALL be created if missing

#### Scenario: Accounts map with nested token object
- **WHEN** a configured account preset is stored
- **THEN** tokens MUST live under `accounts.<preset>.token` with optional `user` and `bot` keys
- **AND** contacts MUST live only under `accounts.<preset>.contacts` as label→email maps
- **AND** optional display metadata such as `name`, `team`, `team_id`, `url`, and `user_id` MAY be present but is not required

### Requirement: Numeric presets without default
Account presets SHALL use numeric string keys and account-scoped commands SHALL require an explicit preset once accounts exist.

#### Scenario: Preset selection requires key
- **WHEN** `slack <preset> …` is invoked
- **THEN** the runtime SHALL select `accounts[<preset>]`
- **AND** if the preset is missing, the command SHALL fail with a clear error rather than falling back to another account

#### Scenario: No root default preset
- **WHEN** accounts are present in config
- **THEN** the CLI MUST NOT apply a config-level default preset for account-scoped commands

### Requirement: Auth configuration commands
The CLI SHALL support listing accounts, writing tokens via `auth`, importing OpenClaw-style credential files, and opening the config in an editor.

#### Scenario: Accounts list
- **WHEN** the user runs `slack accounts list` or `slack accounts list output json`
- **THEN** configured presets SHALL be listed with non-secret identity fields (preset key, name, token presence)
- **AND** raw token values MUST NOT be printed in full (redacted if shown)

#### Scenario: Auth set user and optional bot
- **WHEN** the user runs `slack auth <preset> user <user_token> [bot <bot_token>] [name <name>]`
- **THEN** the preset SHALL be created or updated under `accounts.<preset>`
- **AND** user tokens MUST start with `xoxp-` and bot tokens with `xoxb-`
- **AND** tokens SHALL be stored under `token.user` / `token.bot`

#### Scenario: Auth import from OpenClaw files
- **WHEN** the user runs `slack auth <preset> import`
- **THEN** the runtime SHALL read `~/.openclaw/credentials/slack-bot-token` and `~/.openclaw/credentials/slack-user-token` when present
- **AND** populate the preset token object from those files

#### Scenario: Config prints redacted summary by default
- **WHEN** the user runs `slack config` or `slack config output json`
- **THEN** the runtime SHALL print a redacted account summary equivalent to setup check
- **AND** raw token values MUST NOT appear in terminal output

#### Scenario: Config edit requires interactive TTY
- **WHEN** the user runs `slack config edit` without an interactive TTY
- **THEN** the command SHALL fail closed without opening an editor or dumping config contents
- **WHEN** the user runs `slack config edit` on an interactive TTY
- **THEN** the runtime SHALL open the config path in an editor
- **AND** if the file is missing, it SHALL bootstrap with a minimal `{"accounts": {}}` JSON document

### Requirement: Setup check diagnostics
`slack setup check` SHALL report configuration health without requiring network calls to Slack.

#### Scenario: Setup check summary
- **WHEN** the user runs `slack setup check` or `slack setup check output json`
- **THEN** output SHALL include config path, whether the file exists, account count, per-preset name, bot/user token presence flags, and contact counts
- **AND** it MUST NOT print full token values

### Requirement: Contact management
Contacts SHALL be label→email entries scoped to a single account preset and usable as send/list targets.

#### Scenario: Add contact
- **WHEN** the user runs `slack <preset> contacts add <label> <email>`
- **THEN** the contact SHALL be saved under that preset’s `contacts` map
- **AND** the email MUST contain `@`
- **AND** success output SHALL confirm the label and email

#### Scenario: List contacts
- **WHEN** the user runs `slack <preset> list contacts [output json]`
- **THEN** all saved contacts for that preset SHALL be listed as label/target pairs

#### Scenario: No root-level contacts for preset accounts
- **WHEN** contacts are stored for preset-backed accounts
- **THEN** they MUST NOT be merged into or required from a root-level contacts object

### Requirement: Token resolution precedence
Runtime token resolution SHALL prefer user tokens for human-authored and mark-read paths, with documented fallbacks.

#### Scenario: General token resolution
- **WHEN** a command needs a Slack token
- **THEN** resolution SHALL consider, in order as applicable to the helper: preset `token.user`, env `SLACK_USER_TOKEN` / `SLACK_TOKEN`, preset `token.bot`, env `SLACK_BOT_TOKEN`, then OpenClaw credential files
- **AND** tokens that are neither `xoxp-` nor `xoxb-` SHALL be rejected

#### Scenario: Legacy flat keys remain readable
- **WHEN** legacy flat keys such as `user_token` or `bot_token` exist on the account object
- **THEN** token resolution MAY still read them as fallback inputs
- **AND** new writes via `auth` SHALL use the nested `token` object shape
