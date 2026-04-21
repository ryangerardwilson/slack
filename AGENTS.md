## Workspace Defaults

- Follow `/home/ryan/Documents/agent_context/CLI_TUI_STYLE_GUIDE.md` for CLI/TUI taste and help shape.
- Follow `/home/ryan/Documents/agent_context/CANONICAL_REFERENCE_IMPLEMENTATION_FOR_CLI_AND_TUI_APPS.md` for executable contract details such as `-h`, `-v`, `-u`, installer behavior, release workflow expectations, and regression expectations.
- This file only records `slack`-specific constraints or durable deviations.

## Product Boundaries

- `slack` is a minimal CLI for Slack direct-message workflows through the configured Slack app token.
- Keep the scope narrow: direct message send, contact management, DM listing, DM read-state actions, stale conversation cleanup, optional file delivery, version, and upgrade.
- Do not expand this app into a general Slack client, channel browser, or interactive TUI without explicit user direction.
- Use OpenClaw-style local credential files for Slack token access by default, with `~/.openclaw/credentials/slack-bot-token` as the preferred bot token path.
- `slack ls` scans accessible direct-message conversations for the configured token by default; saved contacts remain useful as labels and targeted filters.
- Use Slack `search.messages` for `ls` with the OpenClaw user-token file by default, because Slack does not allow bot tokens to search across all user DMs. Bot tokens should fall back to `users.conversations` and `conversations.history` only when no user token is available.
- `ls` is message-level direct-message history, not a conversation summary view.

## Interface Rules

- Keep the top-level interface flat: `slack ac <label> <email>`, `slack cfg`, `slack dm <contact_label|email> <message> [file_path] [dir_path]`, `slack df <dm_id> <file_id> [output_path]`, `slack o <dm_id|message_id>`, `slack ls [label] [-ur|-r] [-o] [-l <limit>] [-f <from>] [-c <contains>] [-tl <time_limit>]`, `slack ls rc`, `slack mra`, and `slack sc`.
- Treat short flags as canonical. `-h`, `-v`, and `-u` are reserved for help, version, and upgrade.
- `slack` with no args must print the same help as `slack -h`.
- Help output must stay human-written, compact, and printed with terminal-default styling.
- Do not expose config paths, token internals, or environment-variable inventories in `-h`.

## Architecture Guardrails

- Prefer explicit parsing and explicit Slack API calls over framework-heavy abstractions.
- Keep config as plain JSON under XDG config paths.
- Prefer stdlib where practical; if a third-party dependency remains necessary, keep the local `.venv/` workflow documented in `README.md`.
- Preserve deterministic plain-text success and error output so the tool stays script-friendly.
