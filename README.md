# slack

Minimal CLI for Slack message workflows through configured account presets.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/slack/main/install.sh | bash
```

If `~/.local/bin` is not already on your `PATH`, add it once to `~/.bashrc`
and reload your shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
source ~/.bashrc
```

Canonical installer commands:

```bash
slack -h
slack -v
slack -u
```

`slack -v` prints the installed app version from `_version.py`. Source checkouts may keep a placeholder value until release automation stamps the shipped artifact.

## Setup

Create a local venv and install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Slack account tokens are stored in `~/.config/slack/config.json` as account
presets, similar to the Gmail CLI:

```bash
slack auth 1 -bt xoxb-... -ut xoxp-... -at xapp-... -n personal
slack auth 2 -bt xoxb-... -ut xoxp-... -at xapp-... -n work
slack auth
```

If you already have the older OpenClaw token files, import them into preset `1`:

```bash
slack auth 1 -i
```

Legacy env vars and token files still work as fallback when no account preset is
configured. Prefer config presets for normal use.

When `accounts` exists in the config, account-scoped commands use an explicit
numeric preset like `slack 1 ls` or `slack 2 post ...`. Contacts are stored
inside each preset and are not shared across accounts.

`slack <preset> ls` prioritizes that preset's `user_token` so it can use Slack's
`search.messages` fast path across all user-visible conversations, matching
Lobster's bridge approach. Bot tokens fall back to conversation listing and
history reads for conversations visible to the bot only.

Required practical bot scopes: `chat:write`, `im:write`, `im:read`,
`im:history`, `users:read`, `files:write`. Add `users:read.email` to the bot
token only if it must resolve email contacts without a configured user token.
Recommended for `df` and `ls -o`: `files:read`.
For the user-token fast path, use `search:read`, `im:read`, `im:history`,
`users:read`, `users:read.email`, and `files:read` when attachment reads matter.
For `tui`, add `search:read`, `users:read`, `im:read`, `im:history`,
`mpim:read`, `mpim:history`, `chat:write`, and `files:read` to the user token
scopes.
For event-driven Codex replies, enable Slack Socket Mode, generate an app-level
`xapp-` token with `connections:write`, and subscribe the app to `app_mention`
and `message.im` events.

## Usage

Show help:

```bash
slack -h
```

Add a contact:

```bash
slack 1 ac mom mom@example.com
```

Search saved contacts and Slack workspace users:

```bash
slack 1 su rohan
slack 1 su "rohan choudhary"
```

Open the real config file in your editor:

```bash
slack cfg
```

Post to a saved contact label:

```bash
slack 1 post mom "hello"
```

Post to an email address:

```bash
slack 1 post someone@company.com "hello"
```

Post to a channel or group conversation id:

```bash
slack 1 post C0AE059EU5T "sharing the update here"
```

Post to the same conversation as a message id without replying in-thread:

```bash
slack 1 post C0AE059EU5T:1712764800.000100 "new top-level message in this conversation"
```

Reply in the thread for a message id:

```bash
slack 1 reply C0AE059EU5T:1712764800.000100 "replying in thread"
```

Post with files and directories zipped on the fly:

```bash
slack 1 post design "assets attached" ~/Downloads/mock.png ~/Projects/site/export ~/Downloads/spec.pdf
```

List accessible Slack message history, including message ids, conversation
surface, sender, text, and attachment/embed names:

```bash
slack 1 ls
slack 1 ls 10
slack 1 ls md 10
slack 1 ls -l 20
slack 1 ls -f maanas -tl 2w -l 10
slack 1 ls -c invoice -tl "jan 2025" -l 20
slack 1 ls -ur 10
slack 1 ls md -r 10
slack 1 ls md -o 5
slack 1 ls rc
```

Open a conversation or exact message id, mark it read, download every attachment
on the opened message, show text, and print snippet code blocks:

```bash
slack 1 o D0466D63H7B
slack 1 o D0466D63H7B:1712764800.000100
```

Open the DM/group-DM TUI:

```bash
slack 1 tui
```

The TUI starts on a recent-conversations screen derived from the latest 100
DM/group-DM messages. Use `j`/`k` to move, `l` or Enter to open a conversation,
and `h` from an empty composer to return. The conversation screen hydrates the
latest 100 messages for that DM/GDM, shows attachment/embed names inline, and
lets you type a new message at the bottom. Enter sends, `r` refreshes, and
Ctrl-O opens the latest visible file/embed in `$VISUAL`, `$EDITOR`, then `vim`.

Clear stale conversations and bot-like conversations:

```bash
slack 1 sc
```

`sc` closes DMs whose counterpart has no email or whose latest activity is older than about 6 months. It also leaves joined public channels whose creator has no email or whose channel update time is older than about 6 months. Private channels and group DMs are skipped when the token lacks the required scopes.

Mark all unread DMs as read:

```bash
slack 1 mra
```

Run Slack-to-Codex as an event service:

```bash
slack 1 codex ti
slack 1 codex status
slack 1 codex logs 80
```

The service uses Slack Socket Mode, so it keeps a WebSocket open instead of
polling once a minute. Direct messages to the app and channel mentions are
acknowledged immediately, passed into `codex exec resume`, and answered back in
Slack. Channel mentions are answered in-thread. Personal DMs or mentions of
your Slack user are not delivered through Socket Mode unless they are sent to
the app; the service also runs a short-interval user-token scan for unread
personal DMs and user mentions, then replies from the same Slack CLI service.

`slack 1 ls` scans Slack conversations visible to the configured token. Each row
prints `surface`, `conversation`, and `channel_id` so individual DMs, group DMs,
and channels are distinguishable. The `attachments` row shows file and embed
names only, so `slack 1 o <message_id>` can be used when they need to be opened
or downloaded. Saved contacts are still used for friendly labels such as
`slack 1 ls md -l 10`.
`mra` still operates on contacts you have saved with `ac`.

## Contacts

Account presets, tokens, and contacts are stored in `~/.config/slack/config.json`.
Use a single `token` object per preset. Legacy flat keys such as `bot_token`,
`user_token`, and `app_token` are still readable for older configs, but new auth
writes the compact shape below. Slack team metadata such as `team`, `team_id`,
`url`, and `user_id` is optional and not required for normal CLI operation.

Open that file directly with:

```bash
slack cfg
```

Example:

```json
{
  "accounts": {
    "1": {
      "name": "personal",
      "token": {
        "app": "xapp-...",
        "bot": "xoxb-...",
        "user": "xoxp-..."
      },
      "codex_session_id": "019...",
      "codex_workspace": "/home/ryan",
      "codex_args": ["--skip-git-repo-check", "--full-auto"],
      "codex_prompt": "Respond to the user's query below in view of Ryan's instructions. Query: {}; Instructions: Only respond if it is a data retrieval request from genie in the format {respond:1,response:\"your_response\"}, else respond as {respond:0,response:\"\"}.",
      "contacts": {
        "mom": "mom@example.com"
      }
    },
    "2": {
      "name": "work",
      "token": {
        "bot": "xoxb-...",
        "user": "xoxp-..."
      }
    }
  }
}
```

## Options

- `ac`: Save a contact label for an email address.
- `auth`: List configured account presets.
- `auth <preset> -i`: Import legacy OpenClaw token files into a config preset, including `slack-app-token` when present.
- `auth <preset> -bt <bot_token> [-ut <user_token>] [-at <app_token>] [-n <name>]`: Create or update an account preset with tokens stored in config under `token`.
- `codex once`: Connect to Slack Socket Mode and process one eligible event.
- `codex scan`: Process unread personal DMs once through the user-token watcher.
- `codex service`: Run the long-lived Slack Socket Mode plus personal-DM watcher to Codex service.
- `codex ti` / `codex td`: Install or disable the user systemd service for the preset.
- `codex st` / `codex logs [lines]` / `codex status`: Inspect the event service.
- `codex reset-state`: Clear the local event-service state file.
- `su <query>`: Search saved contact labels/emails and Slack workspace users.
- `cfg`: Open the real config file in `$VISUAL`, then `$EDITOR`, then `vim`.
- `post <target> <message> [path...]`: Post to a saved contact label, email, Slack user id, channel id, or message id. Message ids resolve to their conversation and send a new top-level message. When both tokens are configured, person targets use the user token so saved contacts land in Ryan's actual DMs; explicit channel/message targets use the normal post token path. Files and directories are supported; directories are zipped on the fly.
- `reply <message_id> <message> [path...]`: Reply in the thread for an exact message id, with optional file or directory attachments.
- `df <channel_id> <file_id> [output_path]`: Download an attached file from a conversation by its channel id and file id.
- `o <channel_id|message_id>`: Open a conversation or exact message id, mark it read, print full text, download every attached file/embed, and print snippet code blocks inline. Multiple files/embeds from one message are packaged into one zip.
- `tui`: Open a curses TUI for recent Slack DM/group-DM conversations only. Use `j`/`k` on the conversation list, `l` or Enter to open one, type at the bottom to send, `h` from an empty composer to return, and Ctrl-O to open visible files/embeds.
- `ls`: List the latest 10 accessible Slack messages.
- `ls <number>`: List that many latest accessible Slack messages.
- `ls <label> <number>`: List that many latest DM messages for one saved label.
- `ls -l <limit>`: List a specific number of messages.
- `ls -f <from>`: Filter by sender name, email, user id, or saved contact metadata.
- `ls -c <contains>`: Filter by message text.
- `ls -tl <time_limit>`: Filter by time, using shapes such as `2w`, `14d`, `2025-01`, `"jan 2025"`, `2025-01-10`, or `2025-01-10..2025-01-20`.
- `ls -ur` / `ls -r`: Filter unread or read Slack messages.
- `ls ... -o ...`: For the selected messages, also print full text, download attachments/embeds, and print full snippet code blocks.
- `ls rc`: List all registered contact labels and emails from local config.
- `mra`: Mark all unread saved-contact direct messages as read.
- `sc`: Close stale DMs and leave stale public channels, with explicit skips for unsupported conversation types.
- `-v`: Print version and exit.
- `-u`: Upgrade via the installer script.
- `-h`: Show help.

## Development

Run from source while developing:

```bash
python main.py -h
python main.py -v
```

The installer downloads the latest release binary into `~/.slack/app`.

## Release workflow

Tags like `v0.1.0` trigger GitHub Actions to build `slack-linux-x64.tar.gz` and publish a release.
