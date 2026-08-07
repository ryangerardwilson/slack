# Product Engineer Role

## Purpose

Own slack-specific facts that should not live in root generalists.

## Load Guidance

Load this file for `slack` implementation, CLI/TUI, installer, release, storage, configuration, or project-specific product work.

Root generalists own role behavior. This file owns only project facts and
repo-local operating constraints.

## Owns

- repo-local product and implementation facts
- CLI/TUI contract, command grammar, config, storage, and installer constraints
- release, upgrade, and verification expectations specific to this app

## Project Context

## Product Boundaries

- `slack` is a minimal Go CLI/TUI for Slack direct-message and adjacent message-read workflows through configured account presets.
- Keep the scope narrow: posting to explicit Slack targets, thread replies, contact/user search, contact management, message listing with conversation-surface labels, DM/group-DM TUI reading, cached/API-reported notification read-state actions, stale conversation cleanup, optional file delivery, per-preset event cache sync, version, and upgrade.
- Do not expand this app into a general Slack client or broad interactive TUI without explicit user direction.
- Directory listing uses `slack <preset> list channels`, `list dms`, and `list contacts`. Message history uses `slack <preset> list` or `list messages` with Gmail-style filters. Do not add parallel `contacts list` or `conversations list` commands.
- Use `config.json` account presets as the primary Slack token store. New config writes tokens under `accounts.<preset>.token` with `user` and optional `bot` keys. Legacy flat keys such as `bot_token` and `user_token`, plus OpenClaw-style bot/user credential files, may remain as readable fallback/import inputs.
- Preset keys should be numeric strings such as `1`, `2`, and `3`, matching the Gmail CLI pattern.
- Do not use a config-level default preset. Once `accounts` exists, account-scoped commands should take the preset explicitly.
- Contacts belong only inside account presets as `accounts.<preset>.contacts`; do not add or merge root-level contacts for preset accounts.
- Slack account metadata such as `team`, `team_id`, `url`, and `user_id` is optional display/debug context, not required config.
- For `send`, saved contact labels, raw emails, raw Slack user ids, explicit
  channel ids, channel names (`#blog`), and message ids should use the preset
  user token when present so the configured Slack user is the author on routine human-authored
  Slack writes. Channel-name lookup uses the list/lookup token and
  `users.conversations`.
- `send` with `attach` must use Slack's external upload flow with form-encoded
  `files.getUploadURLExternal`, raw file bytes to the returned upload URL, and
  form-encoded `files.completeUploadExternal` with `initial_comment` for the
  body and no `thread_ts` so caption and files land on one top-level message.
  `reply` with `attach` may thread uploads under the resolved `thread_ts`.
- `slack <preset> list` (or `list messages`) scans accessible message history for the configured token by default; saved contacts remain useful as labels and targeted filters. It must label the surface (`dm`, `group_dm`, `channel`, or `private_channel`) rather than implying every result is a one-to-one DM.
- Message list supports `in <channel_id|#name|name>` (conversation scope) and `from <name>` (sender scope). They may be combined. Channel-scoped list uses `conversations.history` with `oldest` when `since` is set.
- `since` windows accept `Nh`/`Nd`/`Nw`/`Nm`/`Ny`, ISO dates/months, and named months. Invalid windows must error; do not silently ignore them. List results are newest-first after applying the time boundary and limit.
- `slack <preset> thread <message_id>` loads thread replies via `conversations.replies` (root + replies, oldest-first).
- Use Slack `search.messages` for unscoped message `list` with the preset's user token by default, because Slack does not allow bot tokens to search across all user DMs. Bot tokens should fall back to `users.conversations` and `conversations.history` only when no user token is available.
- `list channels` and `list dms` are conversation directories for posting targets, not message history.
- `tui` is a Bubble Tea surface deliberately limited to recent `im,mpim` conversations: a conversation-list screen from the latest 100 DM/GDM messages, plus a full-screen conversation view that hydrates the selected DM/GDM's latest 100 messages. Do not add public/private channel browsing to it without explicit user direction.
- Opening conversations in `tui` marks the configured user's read cursor with the user token. That requires `im:write` for DMs and `mpim:write` for group DMs; read/history scopes alone are insufficient.
- `mark all read` uses `conversations.mark` for conversation read cursors and therefore needs write scopes for the conversation types it clears: `im:write`, `mpim:write`, `groups:write`, and `channels:write` as applicable.
- Slack Activity inbox items are not conversation read cursors and are not supported by official Slack app tokens. `mark all read` must not attempt to clear Activity through browser-session APIs or private Slack endpoints.
- `events sync` warms the per-preset SQLite event cache used by `list`, `open tui`, and mark-read cleanup. `mark all read` must clear cached unread rows as well as Slack API-reported unread counters because Slack's lightweight counters can disagree with the local cache.

## Interface Rules

- Keep the top-level interface flat and declarative: `slack accounts list`, `slack setup check`, `slack auth`, `slack auth <preset> import`, `slack auth <preset> user <user_token> [bot <bot_token>] [name <name>]`, `slack config`, `slack config edit`, `slack <preset> contacts add <label> <email>`, `slack <preset> users search <query>`, `slack <preset> events sync|status|reset cache`, `slack <preset> preview send to <target> body <message> [attach <path> ...]`, `slack <preset> send to <target> body <message> [attach <path> ...]`, `slack <preset> preview reply to <message_id> body <message> [attach <path> ...]`, `slack <preset> reply to <message_id> body <message> [attach <path> ...]`, `slack <preset> files download <channel_id> <file_id> [to <path>]`, `slack <preset> inspect conversation <channel_id>`, `slack <preset> inspect message <message_id>`, `slack <preset> thread <message_id>`, `slack <preset> open conversation <channel_id>`, `slack <preset> open message <message_id>`, `slack <preset> open tui`, `slack <preset> list channels|dms|contacts [output json]`, `slack <preset> list [messages] [filters...] [output json]`, `slack <preset> conversations clean`, `slack <preset> mark all read`, and `slack mark all read`.
- `send` is for a new message in the resolved conversation. `reply` is only for message ids and posts into that message's thread. `reply` is a current declarative command; never treat it as a retired alias.
- `preview` must validate target grammar and attachment paths without resolving
  Slack tokens, opening DMs, posting, uploading files, or marking read state.
  Preview output should include the canonical executable `command` string.
- `inspect` must read message/conversation metadata without marking read or
  downloading attachments. `open` remains the deliberate stateful read/download
  command.
- `slack config` prints a redacted summary only (no raw tokens). Editing requires
  `slack config edit` and a real interactive terminal detected via termios ioctl
  on stdin and stdout (not FileMode char-device bits). Non-interactive edit must
  fail closed without launching an editor or writing config contents to captured
  stdout. The editor process must attach only to `os.Stdin/Stdout/Stderr`.
- Write commands (`send`, `reply`, `edit`, `delete`) and list/thread/preview accept trailing `output json` or `--json` where implemented.
- `verbose` / `--verbose` on list/thread (and channel/dm directories) prints progress to stderr: API attempts, pagination page/cursor, cache hit/miss, and rate-limit waits. Progress must never mix into JSON stdout. Rate-limited Slack calls retry up to 3 times using Retry-After.
- Only `help`, `version`, and `upgrade` remain as global launcher actions for help, version, and upgrade.
- `slack` with no args must print the same help as `slack help`.
- Help output must stay human-written, compact, and printed with terminal-default styling.
- Do not expose config paths, token internals, or environment-variable inventories in `help`.
- Do not reintroduce the retired shared CLI contract package, its TOML file, or old compressed commands.

## Architecture Guardrails

- Prefer explicit parsing and explicit Slack API calls over framework-heavy abstractions.
- Keep config as plain JSON under XDG config paths.
- Prefer stdlib where practical; Go dependencies should stay limited to the terminal UI and SQLite cache.
- Preserve deterministic plain-text success and error output so the tool stays script-friendly.
