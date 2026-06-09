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

- `slack` is a minimal Go CLI/TUI for Slack direct-message and adjacent message-read workflows through the configured Slack app token.
- Keep the scope narrow: posting to explicit Slack targets, thread replies, contact/user search, contact management, message listing with conversation-surface labels, DM/group-DM TUI reading, cached/API-reported notification read-state actions, stale conversation cleanup, optional file delivery, the per-preset event cache service, version, and upgrade.
- Do not expand this app into a general Slack client, channel browser, or broad interactive TUI without explicit user direction.
- Use `config.json` account presets as the primary Slack token store. New config writes tokens under `accounts.<preset>.token` with `app`, `bot`, and `user` keys. Legacy flat keys such as `bot_token`, `user_token`, and `app_token`, plus OpenClaw-style local credential files, may remain as readable fallback/import inputs.
- Preset keys should be numeric strings such as `1`, `2`, and `3`, matching the Gmail CLI pattern.
- Do not use a config-level default preset. Once `accounts` exists, account-scoped commands should take the preset explicitly.
- Contacts belong only inside account presets as `accounts.<preset>.contacts`; do not add or merge root-level contacts for preset accounts.
- Slack account metadata such as `team`, `team_id`, `url`, and `user_id` is optional display/debug context, not required config.
- For `send`, saved contact labels, raw emails, and raw Slack user ids should use the preset user token when present so they land in Ryan's actual Slack DMs. Explicit channel ids and message ids may use the normal bot-first post token path.
- `slack <preset> list` scans accessible message history for the configured token by default; saved contacts remain useful as labels and targeted filters. It must label the surface (`dm`, `group_dm`, `channel`, or `private_channel`) rather than implying every result is a one-to-one DM.
- Use Slack `search.messages` for `list` with the preset's user token by default, because Slack does not allow bot tokens to search across all user DMs. Bot tokens should fall back to `users.conversations` and `conversations.history` only when no user token is available.
- `list` is message-level history, not a conversation summary view or channel browser.
- `tui` is a Bubble Tea surface deliberately limited to recent `im,mpim` conversations: a conversation-list screen from the latest 100 DM/GDM messages, plus a full-screen conversation view that hydrates the selected DM/GDM's latest 100 messages. Do not add public/private channel browsing to it without explicit user direction.
- Opening conversations in `tui` marks Ryan's read cursor with the user token. That requires `im:write` for DMs and `mpim:write` for group DMs; read/history scopes alone are insufficient.
- `mark all read` uses `conversations.mark` for conversation read cursors and therefore needs write scopes for the conversation types it clears: `im:write`, `mpim:write`, `groups:write`, and `channels:write` as applicable.
- Slack Activity inbox items are not conversation read cursors and are not supported by official Slack app tokens. `mark all read` must not attempt to clear Activity through browser-session APIs or private Slack endpoints.
- `events service` owns the per-preset SQLite event cache used by `list`, `open tui`, and mark-read cleanup. `mark all read` must clear cached unread rows as well as Slack API-reported unread counters because Slack's lightweight counters can disagree with the local cache.

## Interface Rules

- Keep the top-level interface flat and declarative: `slack accounts list`, `slack setup check`, `slack auth`, `slack auth <preset> import`, `slack auth <preset> bot <bot_token> [user <user_token>] [app <app_token>] [name <name>]`, `slack config`, `slack <preset> contacts add <label> <email>`, `slack <preset> users search <query>`, `slack <preset> events sync|once|service|timer install|timer disable|status|logs|reset cache`, `slack <preset> preview send to <target> body <message> [attach <path> ...]`, `slack <preset> send to <target> body <message> [attach <path> ...]`, `slack <preset> preview reply to <message_id> body <message> [attach <path> ...]`, `slack <preset> reply to <message_id> body <message> [attach <path> ...]`, `slack <preset> files download <channel_id> <file_id> [to <path>]`, `slack <preset> inspect conversation <channel_id>`, `slack <preset> inspect message <message_id>`, `slack <preset> open conversation <channel_id>`, `slack <preset> open message <message_id>`, `slack <preset> open tui`, `slack <preset> list ... [output json]`, `slack <preset> conversations clean`, `slack <preset> mark all read`, and `slack mark all read`.
- `send` is for a new message in the resolved conversation. `reply` is only for message ids and posts into that message's thread.
- `preview` must validate target grammar and attachment paths without resolving
  Slack tokens, opening DMs, posting, uploading files, or marking read state.
- `inspect` must read message/conversation metadata without marking read or
  downloading attachments. `open` remains the deliberate stateful read/download
  command.
- Only `help`, `version`, and `upgrade` remain as global launcher actions for help, version, and upgrade.
- `slack` with no args must print the same help as `slack help`.
- Help output must stay human-written, compact, and printed with terminal-default styling.
- Do not expose config paths, token internals, or environment-variable inventories in `help`.
- Do not reintroduce the retired shared CLI contract package, its TOML file, or old compressed commands.

## Architecture Guardrails

- Prefer explicit parsing and explicit Slack API calls over framework-heavy abstractions.
- Keep config as plain JSON under XDG config paths.
- Prefer stdlib where practical; Go dependencies should stay limited to the terminal UI, SQLite cache, and Socket Mode transport.
- Preserve deterministic plain-text success and error output so the tool stays script-friendly.
