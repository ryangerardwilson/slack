## Workspace Defaults

- Follow `/home/ryan/Documents/agent_context/CLI_TUI_STYLE_GUIDE.md` for CLI/TUI taste and help shape.
- Follow `/home/ryan/Documents/agent_context/CANONICAL_REFERENCE_IMPLEMENTATION_FOR_CLI_AND_TUI_APPS.md` for executable contract details such as `-h`, `-v`, `-u`, installer behavior, release workflow expectations, and regression expectations.
- This file only records `slack`-specific constraints or durable deviations.

## Product Boundaries

- `slack` is a minimal CLI/TUI for Slack direct-message and adjacent message-read workflows through the configured Slack app token.
- Keep the scope narrow: posting to explicit Slack targets, thread replies, contact/user search, contact management, message listing with conversation-surface labels, DM/group-DM TUI reading, DM read-state actions, stale conversation cleanup, optional file delivery, the Slack Socket Mode plus user-DM polling Codex service, version, and upgrade.
- Do not expand this app into a general Slack client, channel browser, or broad interactive TUI without explicit user direction.
- Use `config.json` account presets as the primary Slack token store. New config writes tokens under `accounts.<preset>.token` with `app`, `bot`, and `user` keys. Legacy flat keys such as `bot_token`, `user_token`, and `app_token`, plus OpenClaw-style local credential files, may remain as readable fallback/import inputs.
- Preset keys should be numeric strings such as `1`, `2`, and `3`, matching the Gmail CLI pattern.
- Do not use a config-level default preset. Once `accounts` exists, account-scoped commands should take the preset explicitly.
- Contacts belong only inside account presets as `accounts.<preset>.contacts`; do not add or merge root-level contacts for preset accounts.
- Slack account metadata such as `team`, `team_id`, `url`, and `user_id` is optional display/debug context, not required config.
- Codex Slack behavior may be narrowed with `accounts.<preset>.codex_prompt`. The prompt supports `{}` and `{query}` placeholders for the Slack message text, and structured replies shaped like `{respond:1,response:"..."}` or `{respond:0,response:""}`.
- For `post`, saved contact labels, raw emails, and raw Slack user ids should use the preset user token when present so they land in Ryan's actual Slack DMs. Explicit channel ids and message ids may use the normal bot-first post token path.
- `slack ls` scans accessible message history for the configured token by default; saved contacts remain useful as labels and targeted filters. It must label the surface (`dm`, `group_dm`, `channel`, or `private_channel`) rather than implying every result is a one-to-one DM.
- Use Slack `search.messages` for `ls` with the preset's user token by default, because Slack does not allow bot tokens to search across all user DMs. Bot tokens should fall back to `users.conversations` and `conversations.history` only when no user token is available.
- `ls` is message-level history, not a conversation summary view or channel browser.
- `tui` is deliberately limited to recent `im,mpim` conversations: a conversation-list screen from the latest 100 DM/GDM messages, plus a full-screen conversation view that hydrates the selected DM/GDM's latest 100 messages and visible files/embeds. Do not add public/private channel browsing to it without explicit user direction.

## Interface Rules

- Keep the top-level interface flat: `slack auth`, `slack auth <preset> -i`, `slack auth <preset> -bt <bot_token> [-ut <user_token>] [-at <app_token>] [-n <name>]`, `slack <preset> ac <label> <email>`, `slack <preset> su <query>`, `slack cfg`, `slack <preset> codex once|scan|service|ti|td|st|logs|status|reset-state`, `slack <preset> post <contact_label|email|message_id|channel_id> <message> [file_path] [dir_path]`, `slack <preset> reply <message_id> <message> [file_path] [dir_path]`, `slack <preset> df <channel_id> <file_id> [output_path]`, `slack <preset> o <channel_id|message_id>`, `slack <preset> tui`, `slack <preset> ls [label] [-ur|-r] [-o] [-l <limit>] [-f <from>] [-c <contains>] [-tl <time_limit>]`, `slack <preset> ls rc`, `slack <preset> mra`, and `slack <preset> sc`.
- `post` is for a new message in the resolved conversation. `reply` is only for message ids and posts into that message's thread.
- Treat short flags as canonical. `-h`, `-v`, and `-u` are reserved for help, version, and upgrade.
- `slack` with no args must print the same help as `slack -h`.
- Help output must stay human-written, compact, and printed with terminal-default styling.
- Do not expose config paths, token internals, or environment-variable inventories in `-h`.

## Architecture Guardrails

- Prefer explicit parsing and explicit Slack API calls over framework-heavy abstractions.
- Keep config as plain JSON under XDG config paths.
- Prefer stdlib where practical; if a third-party dependency remains necessary, keep the local `.venv/` workflow documented in `README.md`.
- Preserve deterministic plain-text success and error output so the tool stays script-friendly.
