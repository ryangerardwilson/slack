# slack

Minimal CLI for Slack direct-message workflows through a configured Slack app token.

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

By default `slack` reads the same OpenClaw-style local bot credential file used
by the wider Slack automation setup:

```bash
mkdir -p ~/.openclaw/credentials
printf '%s\n' 'xoxb-...' > ~/.openclaw/credentials/slack-bot-token
chmod 600 ~/.openclaw/credentials/slack-bot-token
```

You can override that with `SLACK_BOT_TOKEN`, `SLACK_TOKEN`, or config keys such
as `bot_token_file`, `token_file`, and `user_token_file`.

`slack ls` prioritizes the OpenClaw user-token file at
`~/.openclaw/credentials/slack-user-token` so it can use Slack's
`search.messages` fast path across all user-visible DMs, matching Lobster's
bridge approach. Bot tokens fall back to conversation listing and history reads
for conversations visible to the bot only.

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
slack ac mom mom@example.com
```

Search saved contacts and Slack workspace users:

```bash
slack su rohan
slack su "rohan choudhary"
```

Open the real config file in your editor:

```bash
slack cfg
```

Send a DM by saved contact label:

```bash
slack dm mom "hello"
```

Send a DM by email:

```bash
slack dm someone@company.com "hello"
```

Send a DM with a file:

```bash
slack dm boss@company.com "latest draft" ~/Downloads/draft.pdf
```

Send a DM with multiple files:

```bash
slack dm boss@company.com "latest draft" ~/Downloads/draft.pdf ~/Downloads/summary.txt
```

Send a DM with files and directories zipped on the fly:

```bash
slack dm design "assets attached" ~/Downloads/mock.png ~/Projects/site/export ~/Downloads/spec.pdf
```

List accessible DM history, including message ids, sender, text, and attached
file ids:

```bash
slack ls
slack ls 10
slack ls md 10
slack ls -l 20
slack ls -f maanas -tl 2w -l 10
slack ls -c invoice -tl "jan 2025" -l 20
slack ls -ur 10
slack ls md -r 10
slack ls md -o 5
slack ls rc
```

Open a DM or exact message id, mark it read, download every attachment on the
opened message, show text, and print snippet code blocks:

```bash
slack o D0466D63H7B
slack o D0466D63H7B:1712764800.000100
```

Clear stale conversations and bot-like conversations:

```bash
slack sc
```

`sc` closes DMs whose counterpart has no email or whose latest activity is older than about 6 months. It also leaves joined public channels whose creator has no email or whose channel update time is older than about 6 months. Private channels and group DMs are skipped when the token lacks the required scopes.

Mark all unread DMs as read:

```bash
slack mra
```

`slack ls` scans direct-message conversations visible to the configured token.
Saved contacts are still used for friendly labels such as `slack ls md -l 10`.
`mra` still operates on contacts you have saved with `ac`.

## Contacts

Contacts are stored in `~/.config/slack/config.json`.

Open that file directly with:

```bash
slack cfg
```

Example:

```json
{
  "bot_token_file": "~/.openclaw/credentials/slack-bot-token",
  "contacts": {
    "mom": "mom@example.com"
  }
}
```

## Options

- `ac`: Save a contact label for an email address.
- `su <query>`: Search saved contact labels/emails and Slack workspace users.
- `cfg`: Open the real config file in `$VISUAL`, then `$EDITOR`, then `vim`.
- `dm`: Send a DM to a saved contact label or email from the configured Slack app token, with any number of file or directory attachments. Directories are zipped on the fly.
- `df <dm_id> <file_id> [output_path]`: Download an attached file from a DM by its DM id and file id.
- `o <dm_id|message_id>`: Open a DM or exact message id, mark it read, print full text, download every attached file, and print snippet code blocks inline.
- `ls`: List the latest 10 accessible DM messages.
- `ls <number>`: List that many latest accessible DM messages.
- `ls <label> <number>`: List that many latest DM messages for one saved label.
- `ls -l <limit>`: List a specific number of messages.
- `ls -f <from>`: Filter by sender name, email, user id, or saved contact metadata.
- `ls -c <contains>`: Filter by message text.
- `ls -tl <time_limit>`: Filter by time, using shapes such as `2w`, `14d`, `2025-01`, `"jan 2025"`, `2025-01-10`, or `2025-01-10..2025-01-20`.
- `ls -ur` / `ls -r`: Filter unread or read DM messages.
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
