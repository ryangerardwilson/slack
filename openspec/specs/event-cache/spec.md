# event-cache Specification

## Purpose

Define the per-preset SQLite event cache used to warm listings, TUI conversation views, and mark-read cleanup, including sync, status, and reset commands.

## Requirements

### Requirement: Per-preset SQLite cache location
Each account preset SHALL own a SQLite event cache under XDG state (or an account override path).

#### Scenario: Default cache path
- **WHEN** no account override is set
- **THEN** the cache database path SHALL be `$XDG_STATE_HOME/slack/events-<preset-slug>.db` (or `~/.local/state/slack/…` when `XDG_STATE_HOME` is unset)
- **AND** the preset slug SHALL be filesystem-safe

#### Scenario: Account override path
- **WHEN** the account sets `events_cache_db` or `event_cache_db`
- **THEN** that expanded path SHALL be used as the database file

#### Scenario: WAL mode and schema
- **WHEN** the cache is opened
- **THEN** the runtime SHALL enable WAL journal mode
- **AND** initialize tables for conversations, messages, and key/value state including schema version metadata

### Requirement: Events sync warms conversations and messages
`events sync` SHALL fetch recent conversations and a bounded set of conversation histories into the cache.

#### Scenario: Sync writes cache and reports counts
- **WHEN** the user runs `slack <preset> events sync`
- **THEN** the runtime SHALL authenticate with the list token, load recent member conversations (im, mpim, public_channel, private_channel), store conversation rows, and hydrate message history for up to the configured conversation limit (default 20, overridable via account `events_sync_conversation_limit`)
- **AND** print a summary line with conversation count, message count, and cache path

#### Scenario: Last-sync state recorded
- **WHEN** a sync completes
- **THEN** cache state keys such as `last_sync_at`, `last_sync_conversations`, and `last_sync_messages` SHALL be updated

### Requirement: Events status and reset
Operators SHALL be able to inspect cache health and wipe the cache without deleting account config.

#### Scenario: Status JSON
- **WHEN** the user runs `slack <preset> events status`
- **THEN** stdout SHALL be JSON including cache path, existence, user-token presence, and when present row counts and last-sync fields

#### Scenario: Reset cache
- **WHEN** the user runs `slack <preset> events reset cache`
- **THEN** the database file and SQLite WAL/SHM sidecars SHALL be removed
- **AND** a confirmation line with the cache path SHALL be printed

### Requirement: Consumers of the cache
List, TUI, and mark-read paths SHALL be able to use the warmed cache without requiring a live full-history scan on every call.

#### Scenario: List uses cache when warm
- **WHEN** message list filters can be satisfied from cache and the cache has matching rows
- **THEN** list MAY return cache hits without a live search
- **AND** if empty, list SHALL fall back to live APIs

#### Scenario: TUI loads DMs via cache-aware loaders
- **WHEN** `open tui` runs
- **THEN** conversation and message loaders SHALL pass the cache path into recent-conversation and history loaders for im/mpim surfaces
