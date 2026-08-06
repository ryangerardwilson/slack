# read-state Specification

## Purpose

Define conversation read-cursor clearing via `mark all read`, including token/scope requirements, cache coordination, and explicit non-support for Slack Activity inbox items.

## Requirements

### Requirement: Mark all read command shapes
The CLI SHALL support marking unread conversation notifications as read for all configured presets or a single preset.

#### Scenario: All configured presets
- **WHEN** the user runs `slack mark all read`
- **THEN** the runtime SHALL iterate configured account presets that can resolve a mark-read token
- **AND** attempt to clear unread conversation notifications for each

#### Scenario: Single preset
- **WHEN** the user runs `slack <preset> mark all read`
- **THEN** only that preset SHALL be processed

### Requirement: User token and write scopes required
Mark-read SHALL require a user token because conversation read cursors are advanced with `conversations.mark`.

#### Scenario: User token required
- **WHEN** mark all read runs for a preset that only has a bot token
- **THEN** the command SHALL fail with a message that a user token (`xoxp-`) is required along with applicable write scopes (`im:write`, `mpim:write`, `groups:write`, `channels:write`)

#### Scenario: conversations.mark used for cursors
- **WHEN** an unread conversation notification is cleared
- **THEN** the runtime SHALL call Slack `conversations.mark` with the channel and a suitable latest timestamp
- **AND** update the local event cache unread state for that channel when a cache path is available

### Requirement: Cache and API unread sources are both considered
Mark-read SHALL clear both cached unread rows and API-reported unread counters because Slack lightweight counters can disagree with the local cache.

#### Scenario: Merge cache and API candidates
- **WHEN** mark all read gathers targets
- **THEN** it SHALL include conversations with cache `unread_ts > 0` and conversations reported unread by the Slack API
- **AND** merge duplicate channel ids sensibly (preferring fresher latest timestamps and higher unread counts)

#### Scenario: Partial failure is reported
- **WHEN** one or more conversations fail to mark
- **THEN** the command SHALL report failure for the failed count rather than silent success

### Requirement: Activity inbox is out of scope
Slack Activity inbox items are a separate UI surface and MUST NOT be cleared by this CLI.

#### Scenario: No private Activity APIs
- **WHEN** mark all read executes
- **THEN** the implementation MUST NOT attempt browser-session APIs or private Slack endpoints to clear Activity inbox items
- **AND** documentation/help MAY state that Activity is unsupported

### Requirement: TUI open marks DM/GDM read cursor
Opening a conversation in the TUI SHALL advance the configured user’s read cursor for that DM or group DM when possible.

#### Scenario: TUI mark on message view load
- **WHEN** the TUI finishes loading messages for a selected im/mpim conversation
- **THEN** it SHALL call `conversations.mark` with the latest known message timestamp when available
- **AND** update the local cache mark-read state for that channel
