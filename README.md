# slack

Minimal CLI to send Slack direct messages as yourself.

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

Required scopes: `chat:write`, `im:write` (add `users:read` for email lookup).

## Usage

Show help:

```bash
python main.py -h
```

Send a DM by user ID:

```bash
python main.py U123ABC "hello"
```

Send a DM by email (requires `users:read`):

```bash
python main.py someone@company.com "hello"
```

Compose in Vim:

```bash
python main.py -e U123ABC
```

## Labels

Save a label from the CLI:

```bash
python main.py -au mom U123ABC
python main.py -au boss boss@company.com
```

Or edit `~/.config/slack/config.json` directly:

```json
{
  "user_labels": {
    "mom": "U123ABC"
  }
}
```

Then:

```bash
python main.py mom "hello"
```

## Options

- `-e`: Open `$VISUAL`, then `$EDITOR`, to compose a DM.
- `-au`: Save a label pointing to a Slack user ID or email.
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
