# slack

Minimal CLI to save Slack contacts and send direct messages as yourself.

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

Required scopes: `chat:write`, `im:write`, `im:read`, `users:read`, `users:read.email`, `files:write`, `search:read`.

## Usage

Show help:

```bash
python main.py -h
```

Add a contact:

```bash
python main.py ac mom mom@example.com
```

Send a DM by saved contact label:

```bash
python main.py dm mom "hello"
```

Send a DM by email:

```bash
python main.py dm someone@company.com "hello"
```

Send a DM with a file:

```bash
python main.py dm boss@company.com "latest draft" ~/Downloads/draft.pdf
```

Send a DM with a file and a directory zipped on the fly:

```bash
python main.py dm design "assets attached" ~/Downloads/mock.png ~/Projects/site/export
```

List unread DMs:

```bash
python main.py ls -dms
```

List unread mentions:

```bash
python main.py ls -mnts
```

Clear stale conversations and bot-like conversations:

```bash
python main.py sc
```

`sc` closes DMs whose counterpart has no email or whose latest activity is older than about 6 months. It also leaves joined public channels whose creator has no email or whose channel update time is older than about 6 months. Private channels and group DMs are skipped when the token lacks the required scopes.

## Contacts

Contacts are stored in `~/.config/slack/config.json`.

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
- `dm`: Send a DM to a saved contact label or email, with an optional file and optional zipped directory.
- `ls -dms`: List unread direct messages.
- `ls -mnts`: List unread mentions.
- `sc`: Close stale DMs and leave stale public channels, with explicit skips for unsupported conversation types.
- `-v`: Print version and exit.
- `-u`: Upgrade via the installer script.
- `-h`: Show help.

## Shell completion (bash)

For local development:

```bash
source completions/slack.bash
```

For installed binary:

```bash
source ~/.slack/completions/slack.bash
```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/slack/main/install.sh | bash
```

The installer downloads the latest release binary into `~/.slack/app`.

## Release workflow

Tags like `v0.1.0` trigger GitHub Actions to build `slack-linux-x64.tar.gz` and publish a release.
