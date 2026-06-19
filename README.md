# slack

Minimal Slack CLI/TUI for direct-message and adjacent message workflows through configured account presets.

The runtime is a Go CLI with a Bubble Tea TUI at `slack <preset> open tui`.

## Install

```sh
./install.sh help
./install.sh version
./install.sh upgrade
```

The installed launcher is written to `~/.local/bin/slack`. `slack version` prints the runtime version stamped into the Go binary; source checkouts keep `0.0.0` until release automation stamps an artifact.

## Commands

```sh
slack help
slack version
slack upgrade

slack config
slack accounts list
slack setup check
slack auth
slack auth 1 import
slack auth 2 bot xoxb-... user xoxp-... app xapp-... name work
slack 1 contacts add mom mom@example.com
slack 1 list contacts
slack 1 list channels
slack 1 list dms
slack 1 users search rohan
slack 1 preview send to mom body "hello"
slack 1 send to mom body "hello"
slack 1 send to C0AE059EU5T body "sharing the file" attach /abs/path/report.pdf
slack 1 send to C0AE059EU5T body "group update"
slack 1 preview reply to C0AE059EU5T:1712764800.000100 body "reply in thread"
slack 1 reply to C0AE059EU5T:1712764800.000100 body "reply in thread"
slack 1 preview delete message C0AE059EU5T:1712764800.000100
slack 1 delete message C0AE059EU5T:1712764800.000100
slack 1 preview edit message C0AE059EU5T:1712764800.000100 body "corrected text"
slack 1 edit message C0AE059EU5T:1712764800.000100 body "corrected text"
slack 1 files download D0466D63H7B F0AH0LD4133
slack 1 inspect conversation D0466D63H7B
slack 1 inspect message D0466D63H7B:1712764800.000100
slack 1 open conversation D0466D63H7B
slack 1 open message D0466D63H7B:1712764800.000100
slack 1 open tui
slack 1 list unread from maanas since 2w limit 10
slack 1 list unread from maanas since 2w limit 10 output json
slack 1 list containing invoice since "jan 2025" limit 20
slack 1 conversations clean
slack mark all read
slack 1 mark all read
slack 1 events sync
slack 1 events service
slack 1 events timer install
slack 1 events timer disable
slack 1 events status
slack 1 events logs 80
slack 1 events reset cache
```

## Config

`slack config` opens `~/.config/slack/config.json` or `$XDG_CONFIG_HOME/slack/config.json`.

```json
{
  "accounts": {
    "1": {
      "name": "work",
      "token": {
        "bot": "xoxb-...",
        "user": "xoxp-...",
        "app": "xapp-..."
      },
      "contacts": {
        "mom": "mom@example.com"
      }
    }
  }
}
```

User tokens are preferred for listing, channel posts, edits, deletes, file uploads, and person-targeted DMs, and required for marking read state. `send ... attach ...` creates one top-level Slack file message with the body as the file caption. `slack mark all read` marks cached or API-reported unread conversation notifications across configured presets; `slack 1 mark all read` scopes the action to preset `1`. DMs and group DMs require `im:write` and `mpim:write`; channel notifications also require `channels:write` or `groups:write`. Slack Activity inbox items are a separate Slack UI surface and are not attempted by this command. The events service owns the per-preset event cache used by `list`, `open tui`, and mark-read cleanup.
Use `inspect` before `open` when read-state or downloads matter. Use `preview`
before sends or replies when an agent should validate intent without posting.

## Development

```sh
go test ./...
go run ./cmd/slack help
go run ./cmd/slack version
```

The installer can build a local checkout with:

```sh
./install.sh from .
```
