# slack

Minimal CLI to save Slack contacts and send direct messages as yourself.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/slack/main/install.sh | bash
```

Canonical installer commands:

```bash
slack -h
slack -v
slack -u
```

`slack -v` prints the installed app version from the single release version source in `_version.py`.

## Setup

Create a local venv and install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Set a Slack user token:

```bash
export SLACK_TOKEN="xoxp-..."
```

Required scopes: `chat:write`, `im:write`, `im:read`, `im:history`, `users:read`, `users:read.email`, `files:write`.
Recommended for `df`: `files:read`.

## Usage

Show help:

```bash
slack -h
```

Add a contact:

```bash
slack ac mom mom@example.com
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

Send a DM with a file and a directory zipped on the fly:

```bash
slack dm design "assets attached" ~/Downloads/mock.png ~/Projects/site/export
```

List saved-contact DM history, including attached file ids:

```bash
slack ls 10
slack ls md 10
slack ls -ur 10
slack ls md -r 10
slack ls md -o 5
slack ls rc
```

Open a DM, mark it read, show text, download attachments, and print snippet code blocks:

```bash
slack o D0466D63H7B
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

`ls ...` and `mra` only operate on contacts you have saved with `ac`.

## Contacts

Contacts are stored in `~/.config/slack/config.json`.

Open that file directly with:

```bash
slack cfg
```

Example:

```json
{
  "contacts": {
    "mom": "mom@example.com"
  }
}
```

## Options

- `ac`: Save a contact label for an email address.
- `cfg`: Open the real config file in `$VISUAL`, then `$EDITOR`, then `vim`.
- `dm`: Send a DM to a saved contact label or email, with an optional file and optional zipped directory.
- `df <dm_id> <file_id> [output_path]`: Download an attached file from a DM by its DM id and file id.
- `o <dm_id>`: Open a DM, mark it read, print full text, download non-snippet attachments, and print snippet code blocks inline.
- `ls <number>`: List that many latest saved-contact DM messages across all saved labels, showing only email, dm id, and date.
- `ls <label> <number>`: List that many latest saved-contact DM messages for one saved label.
- `ls -ur <number>`: List that many latest unread saved-contact DM messages across all saved labels.
- `ls <label> -ur <number>`: List that many latest unread saved-contact DM messages for one saved label.
- `ls -r <number>`: List that many latest read saved-contact DM messages across all saved labels.
- `ls <label> -r <number>`: List that many latest read saved-contact DM messages for one saved label.
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
