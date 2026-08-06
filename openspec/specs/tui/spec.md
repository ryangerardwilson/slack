# tui Specification

## Purpose

Define the Bubble Tea terminal UI for reading and lightly composing in recent direct-message and group-DM conversations. The TUI is intentionally narrow, not a general Slack client.

## Requirements

### Requirement: Entry point and surface limit
The TUI SHALL launch only via `slack <preset> open tui` and SHALL only present recent `im` and `mpim` conversations.

#### Scenario: Open tui
- **WHEN** the user runs `slack <preset> open tui`
- **THEN** the runtime SHALL start an alt-screen Bubble Tea program for that preset’s token identity
- **AND** conversation loading MUST use conversation types `im,mpim` only

#### Scenario: No channel browsing
- **WHEN** the TUI conversation list is populated
- **THEN** public and private channels MUST NOT appear as browsable rows
- **AND** channel browsing MUST NOT be added without explicit product direction

### Requirement: Conversation list and message view
The TUI SHALL provide a conversation-list screen and a full-screen conversation view backed by limited recent history.

#### Scenario: Conversation list size
- **WHEN** conversations are loaded
- **THEN** the loader SHALL request up to the latest 100 DM/GDM conversations (as implemented by `loadRecentConversations` with limit 100)

#### Scenario: Message hydration
- **WHEN** the user opens a conversation
- **THEN** the TUI SHALL load up to the latest 100 messages for that conversation
- **AND** display sender, timestamp, and text

#### Scenario: Navigation keys
- **WHEN** the TUI is on the conversation list
- **THEN** `j`/`down` and `k`/`up` SHALL move selection, `enter`/`l` SHALL open messages, `r` SHALL reload the list, and `q`/`ctrl+c` SHALL quit

#### Scenario: Message view keys
- **WHEN** the TUI is in message view
- **THEN** `h`/`esc` SHALL return to the conversation list and `i` SHALL enter compose mode

### Requirement: Compose sends top-level messages in the open DM/GDM
The TUI compose path SHALL post a new top-level message in the selected conversation.

#### Scenario: Compose and send
- **WHEN** the user enters compose mode, types text, and presses enter
- **THEN** the TUI SHALL call `chat.postMessage` on the selected channel without thread_ts
- **AND** on success clear the composer and reload messages
- **AND** empty enter SHALL cancel compose without sending

### Requirement: Read cursor on open
Selecting a conversation and loading messages SHALL mark the conversation read for the configured user when write scopes allow.

#### Scenario: Mark after load
- **WHEN** messages finish loading for a conversation with a non-empty id
- **THEN** the mark-read callback SHALL run using the latest message timestamp and the user token’s `conversations.mark` capability
