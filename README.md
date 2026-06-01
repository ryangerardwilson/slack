# slack

Minimal Slack CLI/TUI for direct-message and adjacent message workflows through configured account presets.

## Install

```sh
./install.sh help
./install.sh version
./install.sh upgrade
```

The installed launcher is written to `~/.local/bin/slack`. `slack version` prints the runtime version from `_version.py`; source checkouts keep `0.0.0` until release automation stamps an artifact.

## Commands

```sh
slack help
slack version
slack upgrade

slack config
slack auth
slack auth 1 import
slack auth 2 bot xoxb-... user xoxp-... app xapp-... name work
slack 1 contacts add mom mom@example.com
slack 1 contacts list
slack 1 users search rohan
slack 1 send to mom body "hello"
slack 1 send to C0AE059EU5T body "group update"
slack 1 reply to C0AE059EU5T:1712764800.000100 body "reply in thread"
slack 1 files download D0466D63H7B F0AH0LD4133
slack 1 open conversation D0466D63H7B
slack 1 open message D0466D63H7B:1712764800.000100
slack 1 open tui
slack 1 list unread from maanas since 2w limit 10
slack 1 list containing invoice since "jan 2025" limit 20
slack 1 conversations clean
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

User tokens are preferred for listing and person-targeted DMs. The events service owns the per-preset DM/GDM cache used by `list` and `open tui`.
