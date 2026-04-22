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
slack auth 1 -bt xoxb-... -ut xoxp-... -n personal
slack auth 2 -bt xoxb-... -ut xoxp-... -n work
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
`im:history`, `users:read`, `users:read.email`, `files:write`.
Recommended for `df` and `ls -o`: `files:read`.
For the user-token fast path, use `search:read`, `im:read`, `im:history`,
`users:read`, `users:read.email`, and `files:read` when attachment reads matter.

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
surface, sender, text, and attached file ids:

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

Clear stale conversations and bot-like conversations:

```bash
slack 1 sc
```

`sc` closes DMs whose counterpart has no email or whose latest activity is older than about 6 months. It also leaves joined public channels whose creator has no email or whose channel update time is older than about 6 months. Private channels and group DMs are skipped when the token lacks the required scopes.

Mark all unread DMs as read:

```bash
slack 1 mra
```

`slack 1 ls` scans Slack conversations visible to the configured token. Each row
prints `surface`, `conversation`, and `channel_id` so individual DMs, group DMs,
and channels are distinguishable. Saved contacts are still used for friendly
labels such as `slack 1 ls md -l 10`.
`mra` still operates on contacts you have saved with `ac`.

## Contacts

Account presets, tokens, and contacts are stored in `~/.config/slack/config.json`.

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
      "bot_token": "xoxb-...",
      "user_token": "xoxp-...",
      "contacts": {
        "mom": "mom@example.com"
      }
    },
    "2": {
      "name": "work",
      "bot_token": "xoxb-...",
      "user_token": "xoxp-..."
    }
  }
}
```

## Options

- `ac`: Save a contact label for an email address.
- `auth`: List configured account presets.
- `auth <preset> -i`: Import legacy OpenClaw token files into a config preset.
- `auth <preset> -bt <bot_token> [-ut <user_token>] [-n <name>]`: Create or update an account preset with tokens stored in config.
- `su <query>`: Search saved contact labels/emails and Slack workspace users.
- `cfg`: Open the real config file in `$VISUAL`, then `$EDITOR`, then `vim`.
- `post <target> <message> [path...]`: Post to a saved contact label, email, Slack user id, channel id, or message id. Message ids resolve to their conversation and send a new top-level message. Files and directories are supported; directories are zipped on the fly.
- `reply <message_id> <message> [path...]`: Reply in the thread for an exact message id, with optional file or directory attachments.
- `df <channel_id> <file_id> [output_path]`: Download an attached file from a conversation by its channel id and file id.
- `o <channel_id|message_id>`: Open a conversation or exact message id, mark it read, print full text, download every attached file, and print snippet code blocks inline.
- `ls`: List the latest 10 accessible Slack messages.
- `ls <number>`: List that many latest accessible Slack messages.
- `ls <label> <number>`: List that many latest DM messages for one saved label.
- `ls -l <limit>`: List a specific number of messages.
- `ls -f <from>`: Filter by sender name, email, user id, or saved contact metadata.
- `ls -c <contains>`: Filter by message text.
- `ls -tl <time_limit>`: Filter by time, using shapes such as `2w`, `14d`, `2025-01`, `"jan 2025"`, `2025-01-10`, or `2025-01-10..2025-01-20`.
- `ls -ur` / `ls -r`: Filter unread or read Slack messages.
- `ls ... -o ...`: For the selected messages, also print full text, download non-snippet attachments, and print full snippet code blocks.
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
