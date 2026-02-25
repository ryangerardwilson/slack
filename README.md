# slack

Minimal CLI to send Slack direct messages as yourself.

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Set a Slack user token:

```bash
export SLACK_TOKEN="xoxp-..."
```

Required scopes: `chat:write`, `im:write` (add `users:read` for email lookup).

## Usage

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

Create `~/.config/slack/config.json`:

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

Add a label from the CLI (user ID or email). Email labels require `users:read`.

```bash
python main.py -au mom U123ABC
python main.py -au boss boss@company.com
```

## Options

- `-e`, `--edit`: Open $EDITOR to compose a DM.
- `-au`, `--add-user`: Save a label pointing to a Slack user ID or email.
- `-v`, `--version`: Print version and exit.
- `-u`, `--upgrade`: Upgrade via the installer script.
- `-h`, `--help`: Show help.

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
