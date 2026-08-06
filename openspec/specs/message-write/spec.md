# message-write Specification

## Purpose

Define posting, threading, edit, delete, attachment upload, and dry-run preview behavior for Slack writes performed by the CLI.

## Requirements

### Requirement: Send creates a new top-level message
`send` SHALL post a new top-level message (or file message) to a resolved conversation target and SHALL NOT require a message id.

#### Scenario: Send text body
- **WHEN** the user runs `slack <preset> send to <target> body <message>`
- **THEN** the runtime SHALL resolve the target, post via `chat.postMessage` without `thread_ts`, and print a plain-text success line including `posted`, target, kind, channel, and timestamp when available

#### Scenario: Send target resolution
- **WHEN** a send target is resolved
- **THEN** the runtime SHALL accept saved contact labels, raw emails, Slack user ids (`U…`/`W…`), conversation ids (`C…`/`D…`/`G…`), and channel names (`#name` or bare name)
- **AND** contact labels SHALL expand via the preset contacts map before further resolution
- **AND** user/email targets SHALL open a DM channel before posting

#### Scenario: User token preferred for human-authored posts
- **WHEN** a user token is configured for the preset
- **THEN** routine human-authored writes (including person-targeted DMs and channel posts as implemented by write helpers) SHALL prefer the user token so the configured Slack user is the author
- **AND** channel-name lookup SHALL use the list/lookup token and member conversation listing

### Requirement: Reply posts only into a message thread
`reply` SHALL accept only a message id (`channel_id:ts`) and post into that message’s thread.

#### Scenario: Reply to message id
- **WHEN** the user runs `slack <preset> reply to <channel_id>:<ts> body <message> [attach <path> ...]`
- **THEN** the runtime SHALL resolve the thread timestamp for that message, post with `thread_ts`, optionally upload attachments into the thread, and print a success line including `replied`, message id, channel, and thread_ts

#### Scenario: Reply rejects non-message targets
- **WHEN** `reply` is invoked with a target that is not a message id
- **THEN** the CLI SHALL return a usage error describing the `channel_id:ts` shape

### Requirement: Edit and delete by message id
The CLI SHALL support editing message text and deleting messages identified by message id.

#### Scenario: Delete message
- **WHEN** the user runs `slack <preset> delete message <channel_id>:<ts>`
- **THEN** the runtime SHALL call Slack delete for that channel and timestamp
- **AND** print a success line with `deleted`, message id, and channel

#### Scenario: Edit message
- **WHEN** the user runs `slack <preset> edit message <channel_id>:<ts> body <message>`
- **THEN** the runtime SHALL update the message text via Slack
- **AND** print a success line with `edited`, message id, and channel

### Requirement: Attachment upload via external upload flow
File attachments SHALL use Slack’s external upload flow so caption and files land as intended for top-level send versus threaded reply.

#### Scenario: Send with attach creates top-level file message
- **WHEN** the user runs `send … attach <path>…` with one or more existing paths
- **THEN** the runtime SHALL use form-encoded `files.getUploadURLExternal`, upload raw file bytes to the returned URL, and complete with form-encoded `files.completeUploadExternal`
- **AND** the body SHALL be supplied as `initial_comment`
- **AND** completion MUST omit `thread_ts` so caption and files land on one top-level message

#### Scenario: Reply with attach may thread uploads
- **WHEN** the user runs `reply … attach <path>…`
- **THEN** uploads MAY be completed under the resolved `thread_ts`

#### Scenario: Missing body and attachments rejected on send
- **WHEN** `send` is invoked with neither a non-empty body nor attachment paths
- **THEN** the CLI SHALL return a usage error

#### Scenario: Directory attachments are zipped
- **WHEN** an attachment path is a directory
- **THEN** the runtime SHALL zip the directory contents for upload rather than failing on directory upload

### Requirement: Preview is side-effect free
`preview` variants SHALL validate grammar and local attachment paths without contacting Slack for token resolution, DM open, post, upload, or read-state changes.

#### Scenario: Preview send
- **WHEN** the user runs `slack <preset> preview send to <target> body <message> [attach <path> ...]`
- **THEN** the CLI SHALL verify attachment paths exist when provided
- **AND** print a dry-run section with action `send`, target, inferred target_kind, resolved label/value, body, and attachments
- **AND** MUST NOT call Slack APIs

#### Scenario: Preview reply, edit, delete
- **WHEN** the user runs `preview reply`, `preview edit message`, or `preview delete message` with valid shapes
- **THEN** the CLI SHALL print a dry-run section describing the intended action and identifiers
- **AND** include a canonical executable `command` string matching the non-preview form
- **AND** MUST NOT call Slack APIs

#### Scenario: Write commands accept output json
- **WHEN** `send`, `reply`, `edit`, or `delete` is invoked with trailing `output json`
- **THEN** success output SHALL be a JSON object with action, identifiers, and timestamps rather than only a plain key=value line
