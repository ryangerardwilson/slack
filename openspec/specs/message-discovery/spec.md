# message-discovery Specification

## Purpose

Define conversation directories, message history listing/search, inspect vs open semantics, user search, file download, and stale conversation cleanup.

## Requirements

### Requirement: Directory listing vs message history
The CLI SHALL separate conversation directories (`list channels`, `list dms`, `list contacts`) from message history (`list` / `list messages`).

#### Scenario: List channels
- **WHEN** the user runs `slack <preset> list channels [output json]`
- **THEN** the runtime SHALL list member channels suitable as send targets (ids/names)
- **AND** MUST NOT treat this as message history

#### Scenario: List dms
- **WHEN** the user runs `slack <preset> list dms [output json]`
- **THEN** the runtime SHALL list DM and group-DM conversation ids for posting/reading targets

#### Scenario: List contacts is registry only
- **WHEN** the user runs `slack <preset> list contacts [output json]`
- **THEN** only saved contact labels for the preset SHALL be listed
- **AND** parallel commands such as `contacts list` or `conversations list` MUST NOT be required aliases for this behavior

### Requirement: Message list with Gmail-style filters
`slack <preset> list` (or `list messages`) SHALL scan accessible message history with optional filters and surface labels.

#### Scenario: Default list behavior
- **WHEN** the user runs `slack <preset> list` or `slack <preset> list messages` without filters
- **THEN** the runtime SHALL return recent accessible messages up to the default limit (10 unless `limit` overrides)
- **AND** each result SHALL label the conversation surface as one of `dm`, `group_dm`, `channel`, or `private_channel` rather than implying every row is a 1:1 DM

#### Scenario: Supported filters
- **WHEN** list filters are supplied
- **THEN** the CLI SHALL accept `unread` or `read`, `in <channel_id|#name|name>`, `for <label>`, `from <sender>`, `containing <text>`, `since <window>`, `limit <count>`, optional `open`, and optional `output json`
- **AND** `from` filters senders and `in` filters conversations; both MAY be combined
- **AND** `open` and `output json` MUST NOT be combined

#### Scenario: Time windows
- **WHEN** `since` is provided
- **THEN** relative forms such as `4h` / `2w` / `1d` / `3m` / `1y`, ISO dates/months, and named month-year forms SHALL be accepted
- **AND** malformed windows MUST produce a usage error rather than being ignored
- **AND** the time boundary MUST be applied before limiting results
- **AND** default list order SHALL be newest-first

#### Scenario: Channel-scoped history
- **WHEN** `list in <channel>` is used
- **THEN** the runtime SHALL resolve channel ids and `#name` targets
- **AND** prefer `conversations.history` (with `oldest` when `since` is set) for that conversation rather than workspace-wide scan only

#### Scenario: Search prefers user-token search.messages for unscoped list
- **WHEN** a user token is available for listing and no `in` channel filter is set
- **THEN** live message search SHALL prefer Slack `search.messages`
- **AND** if only a bot token is available, the runtime MAY fall back to `users.conversations` plus `conversations.history` scanning

#### Scenario: Cache-first then live
- **WHEN** filters do not require cache bypass
- **THEN** the runtime SHALL consult the per-preset event cache first
- **AND** if the cache yields no matches, it SHALL fall back to live search

#### Scenario: Thread history command
- **WHEN** the user runs `slack <preset> thread <message_id> [limit <count>] [output json]`
- **THEN** the runtime SHALL fetch `conversations.replies` for that root
- **AND** output root and replies with timestamps, senders, text, and optional pagination cursor
- **AND** thread chronology SHALL be oldest-first (root first)

### Requirement: Inspect is read-only; open is stateful
`inspect` SHALL report metadata without side effects; `open` SHALL perform deliberate read-state and download side effects.

#### Scenario: Inspect conversation or message
- **WHEN** the user runs `slack <preset> inspect conversation <channel_id>` or `inspect message <channel_id>:<ts>`
- **THEN** the runtime SHALL print conversation/message metadata
- **AND** MUST NOT mark the conversation read
- **AND** MUST NOT download attachments as a side effect of inspect

#### Scenario: Open conversation or message
- **WHEN** the user runs `slack <preset> open conversation <channel_id>` or `open message <channel_id>:<ts>`
- **THEN** the runtime SHALL present the content as a deliberate read action
- **AND** MAY mark read cursor / update local cache as part of open

### Requirement: File download
The CLI SHALL support downloading a Slack file by channel and file id.

#### Scenario: Download file
- **WHEN** the user runs `slack <preset> files download <channel_id> <file_id> [to <path>]`
- **THEN** the runtime SHALL download the file with the list/read token
- **AND** write it to the requested path or a default derived path

### Requirement: Users search
The CLI SHALL search workspace users and local contacts by query string.

#### Scenario: Users search
- **WHEN** the user runs `slack <preset> users search <query>`
- **THEN** matching local contacts and Slack users SHALL be listed for selection as targets

### Requirement: Conversations clean
`conversations clean` SHALL close or leave stale conversations per implemented heuristics.

#### Scenario: Stale DM cleanup
- **WHEN** the user runs `slack <preset> conversations clean`
- **THEN** the runtime SHALL evaluate member DMs (and other implemented surfaces) against staleness heuristics such as missing email or inactivity beyond six months
- **AND** attempt Slack close/leave actions for qualifying conversations
- **AND** report per-conversation actions in plain-text sections
