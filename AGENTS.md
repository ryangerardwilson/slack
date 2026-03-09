## Product Boundaries

- `slack` is a minimal CLI for sending Slack direct messages as the authenticated user.
- Keep the scope narrow: direct message send, editor composition, label management, version, and upgrade.
- Do not expand this app into a general Slack client, channel browser, or interactive TUI without explicit user direction.

## Interface Rules

- Keep the top-level interface flat: `slack <recipient> <text...>`, `slack -e <recipient>`, `slack -au <label> <user_id|email>`.
- Treat short flags as canonical. `-h`, `-v`, and `-u` are reserved for help, version, and upgrade.
- `slack` with no args must print the same help as `slack -h`.
- Help output must stay human-written, compact, and styled in muted gray only on real TTYs.
- Do not expose config paths, token internals, or environment-variable inventories in `-h`.

## Architecture Guardrails

- Prefer explicit parsing and explicit Slack API calls over framework-heavy abstractions.
- Keep config as plain JSON under XDG config paths.
- Prefer stdlib where practical; if a third-party dependency remains necessary, keep the local `.venv/` workflow documented in `README.md`.
- Preserve deterministic plain-text success and error output so the tool stays script-friendly.
