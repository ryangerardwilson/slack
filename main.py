import json
import mimetypes
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import zipfile
import fcntl
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from _version import __version__
from rgw_cli_contract import (
    AppSpec,
    open_config_in_editor,
    resolve_install_script_path,
    run_app,
)

USER_TOKEN_PREFIXES = ("xoxp-", "xoxc-")
BOT_TOKEN_PREFIX = "xoxb-"
APP_TOKEN_PREFIX = "xapp-"
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]+$")
CONVERSATION_ID_RE = re.compile(r"^[CDG][A-Z0-9]+$")
COMMANDS = {
    "ac",
    "auth",
    "codex",
    "cfg",
    "conf",
    "df",
    "dm",
    "event",
    "events",
    "ls",
    "mra",
    "o",
    "post",
    "reply",
    "sc",
    "su",
    "tui",
    "u",
}
DEFAULT_BOT_TOKEN_FILE = "~/.openclaw/credentials/slack-bot-token"
DEFAULT_USER_TOKEN_FILE = "~/.openclaw/credentials/slack-user-token"
DEFAULT_APP_TOKEN_FILE = "~/.openclaw/credentials/slack-app-token"
DEFAULT_LIST_LIMIT = 10
DEFAULT_CODEX_ARGS = ["--skip-git-repo-check", "--full-auto"]
EVENT_CACHE_SCHEMA_VERSION = 1
EVENT_SYNC_CONVERSATION_LIMIT = 20
EVENT_SOCKET_TIMEOUT_SECONDS = 70
EVENT_SYNC_SECONDS = 120
_RELATIVE_TIME_RE = re.compile(r"^(?P<amount>\d+)(?P<unit>[dwmy])$", re.IGNORECASE)
_ISO_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")
_ISO_DATE_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")
_NAMED_MONTH_RE = re.compile(r"^(?P<month>[A-Za-z]+)[ -]+(?P<year>\d{4})$", re.IGNORECASE)
_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_TIME_LIMIT_SHAPE = '2w | 14d | 3m | 1y | 2025-01 | "jan 2025" | 2025-01-10 | 2025-01-10..2025-01-20'
INSTALL_SCRIPT = resolve_install_script_path(__file__)
CONFIG_BOOTSTRAP_TEXT = '{\n  "accounts": {}\n}\n'
HELP_TEXT = """Slack CLI

flags:
  slack -h
    show this help
  slack -v
    print the installed version
  slack -u
    upgrade to the latest release

features:
  save a contact label for a frequently used Slack recipient
  # slack <preset> ac <label> <email>
  slack 1 ac mom mom@example.com
  slack 1 ac boss boss@company.com

  edit the saved-contact config directly in your editor
  # slack conf
  slack conf

  configure Slack account presets with tokens stored in config.json
  # slack auth
  # slack auth <preset> -i
  # slack auth <preset> -bt <bot_token> [-ut <user_token>] [-at <app_token>] [-n <name>]
  slack auth
  slack auth 1 -i
  slack auth 2 -bt xoxb-... -ut xoxp-... -at xapp-... -n work

  run the event-based Slack to Codex bridge
  # slack <preset> codex once|scan|service|ti|td|st|logs|status|reset-state
  slack 1 codex ti
  slack 1 codex status
  slack 1 codex logs 80

  keep a local realtime DM/GDM event cache for faster ls/tui loads
  # slack <preset> events sync|once|service|ti|td|st|logs|status|reset-cache
  slack 1 events ti
  slack 1 events status

  post a message from a configured Slack account to a contact, channel, or conversation
  # slack <preset> post <contact_label|email|message_id|channel_id> <message> [path...]
  slack 1 post mom "hello"
  slack 1 post boss@company.com "latest draft" ~/Downloads/draft.pdf
  slack 1 post C0AE059EU5T "group update"
  slack 1 post C0AE059EU5T:1712764800.000100 "same conversation, new top-level message"

  reply in the thread for an exact Slack message id
  # slack <preset> reply <message_id> <message> [path...]
  slack 1 reply C0AE059EU5T:1712764800.000100 "reply in thread"

  download a file attachment from a conversation by channel_id and file_id
  # slack <preset> df <channel_id> <file_id> [output_path]
  slack 1 df D0466D63H7B F0AH0LD4133

  open a conversation or exact message id, mark it read, show text, download files, and print code blocks
  # slack <preset> o <channel_id|message_id>
  slack 1 o D0466D63H7B
  slack 1 o D0466D63H7B:1712764800.000100

  open a keyboard-first terminal view for the latest 100 DM/group-DM messages
  # slack <preset> tui
  slack 1 tui

  list Slack message history with Gmail-style filters, surface labels, and attachment names
  # slack <preset> ls [label] [-ur|-r] [-o] [-l <limit>] [-f <from>] [-c <contains>] [-tl <time_limit>]
  slack 1 ls
  slack 1 ls 10
  slack 1 ls md 10
  slack 1 ls -l 20
  slack 1 ls -f maanas -tl 2w -l 10
  slack 1 ls -c invoice -tl "jan 2025" -l 20
  slack 1 ls -ur 10
  slack 1 ls md -r 10
  slack 1 ls md -o 5

  list all registered contact labels
  # slack <preset> ls rc
  slack 1 ls rc

  search saved contacts and Slack workspace users
  # slack <preset> su <query>
  slack 1 su rohan
  slack 1 su "rohan choudhary"

  clear stale conversations and bot-like conversations
  # slack <preset> sc
  slack 1 sc

  mark all unread saved-contact direct messages as read
  # slack <preset> mra
  slack 1 mra
"""


def get_env(name):
    value = os.getenv(name)
    if value:
        return value
    return None


def get_config_path(config_override=None):
    if config_override:
        return os.path.expanduser(config_override)
    base = os.getenv("XDG_CONFIG_HOME")
    if not base:
        base = os.path.expanduser("~/.config")
    base = os.path.expanduser(base)
    return os.path.join(base, "slack", "config.json")


def load_config(config_path):
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read config at {config_path}: {exc}")

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise SystemExit("Config file must contain a JSON object.")
    return payload


def save_config(config_path, payload):
    directory = os.path.dirname(config_path)
    os.makedirs(directory, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        os.chmod(directory, 0o700)
        os.chmod(config_path, 0o600)
    except OSError:
        pass


def _accounts(config):
    accounts = config.get("accounts")
    if accounts is None:
        return {}
    if not isinstance(accounts, dict):
        raise SystemExit("accounts must be a JSON object.")
    return accounts


def _sorted_presets(accounts):
    def key(value):
        text = str(value)
        return (0, int(text)) if text.isdigit() else (1, text)

    return sorted((str(item) for item in accounts), key=key)


def select_account(config, preset=None):
    accounts = _accounts(config)
    if not accounts:
        if preset:
            raise SystemExit(f"Preset '{preset}' not found in config.")
        return None, config

    selected = (preset or "").strip()
    if not selected:
        available = ", ".join(_sorted_presets(accounts)) or "-"
        raise SystemExit(f"Missing preset. Use: slack <preset> <command>. Available presets: {available}")
    account = accounts.get(selected)
    if not isinstance(account, dict):
        available = ", ".join(_sorted_presets(accounts)) or "-"
        raise SystemExit(f"Preset '{selected}' not found in config. Available presets: {available}")
    return selected, account


def normalize_contacts(payload):
    labels = payload.get("contacts")
    if labels is None:
        labels = payload.get("user_labels", {})
    if labels is None:
        return {}
    if not isinstance(labels, dict):
        raise SystemExit("contacts must be a JSON object.")

    cleaned = {}
    for key, value in labels.items():
        if (
            isinstance(key, str)
            and isinstance(value, str)
            and key.strip()
        ):
            value = value.strip()
            if value:
                cleaned[key.strip()] = value
    return cleaned


def contacts_for_account(config, account):
    return normalize_contacts(account)


def save_contact(config, preset, label, target):
    if preset:
        accounts = config.setdefault("accounts", {})
        account = accounts.setdefault(preset, {})
        contacts = normalize_contacts(account)
        contacts[label] = target
        account["contacts"] = contacts
        return

    contacts = normalize_contacts(config)
    contacts[label] = target
    config["contacts"] = contacts
    if "user_labels" in config:
        del config["user_labels"]


def _requests():
    import requests

    return requests


def _ls_usage():
    return (
        "Use: slack <preset> ls rc | slack <preset> ls [label] [-ur|-r] [-o] "
        "[-l <limit>] [-f <from>] [-c <contains>] [-tl <time_limit>]"
    )


def _top_level_usage():
    return (
        "Use: slack auth [<preset> -i|-bt <bot_token> [-ut <user_token>] [-at <app_token>]] | "
        "slack <preset> ac <label> <email> | slack <preset> su <query> | slack conf | "
        "slack <preset> codex once|scan|service|ti|td|st|logs|status|reset-state | "
        "slack <preset> events sync|once|service|ti|td|st|logs|status|reset-cache | "
        "slack <preset> post <contact_label|email|message_id|channel_id> <message> [path...] | "
        "slack <preset> reply <message_id> <message> [path...] | "
        "slack <preset> df <channel_id> <file_id> [output_path] | "
        "slack <preset> o <channel_id|message_id> | slack <preset> tui | slack <preset> ls rc | "
        "slack <preset> ls [label] [-ur|-r] [-o] [-l <limit>] [-f <from>] "
        "[-c <contains>] [-tl <time_limit>] | slack <preset> sc | slack <preset> mra"
    )


def _parse_positive_int(value, label):
    try:
        parsed = int(value)
    except ValueError:
        raise SystemExit(f"{label} must be a positive integer")
    if parsed <= 0:
        raise SystemExit(f"{label} must be > 0")
    return parsed


def parse_args(argv):
    args = {
        "command": None,
        "preset": None,
        "label": None,
        "email": None,
        "recipient": None,
        "message": None,
        "file_id": None,
        "output_path": None,
        "paths": [],
        "open_mode": False,
        "ls_label": None,
        "ls_registry": False,
        "ls_filter": "all",
        "ls_limit": DEFAULT_LIST_LIMIT,
        "ls_from": None,
        "ls_contains": None,
        "ls_time_limit": None,
        "query": None,
        "auth_preset": None,
        "auth_bot_token": None,
        "auth_user_token": None,
        "auth_app_token": None,
        "auth_name": None,
        "auth_import": False,
        "auth_list": False,
        "codex_action": None,
        "codex_lines": 80,
        "events_action": None,
        "events_lines": 80,
        "config": None,
        "version": False,
        "upgrade": False,
    }

    if not argv:
        return args

    index = 0
    if len(argv) >= 2 and argv[0].isdigit() and argv[1] in COMMANDS:
        args["preset"] = argv[0]
        index = 1
    while index < len(argv):
        token = argv[index]

        if token == "-cfg":
            if index + 1 >= len(argv):
                raise SystemExit("Use: slack -cfg <config_path>")
            args["config"] = argv[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            raise SystemExit(f"Unknown flag: {token}")

        if args["command"] is not None:
            raise SystemExit(_top_level_usage())

        args["command"] = token
        remaining = argv[index + 1 :]
        if token == "ac":
            if len(remaining) != 2:
                raise SystemExit("Use: slack ac <label> <email>")
            args["label"], args["email"] = remaining
            return args
        if token in {"cfg", "conf"}:
            if remaining:
                raise SystemExit("Use: slack conf")
            return args
        if token == "auth":
            if not remaining:
                if args["preset"]:
                    args["auth_preset"] = args["preset"]
                    return args
                args["auth_list"] = True
                return args
            if args["preset"] and remaining[0].startswith("-"):
                auth_preset = args["preset"]
                i = 0
            else:
                auth_preset = remaining[0]
                i = 1
            if not auth_preset or auth_preset.startswith("-"):
                raise SystemExit(
                    "Use: slack auth <preset> [-i]|[-bt <bot_token>] [-ut <user_token>] [-at <app_token>] [-n <name>]"
                )
            args["auth_preset"] = auth_preset
            while i < len(remaining):
                item = remaining[i]
                if item == "-i":
                    args["auth_import"] = True
                    i += 1
                    continue
                if item == "-bt":
                    if i + 1 >= len(remaining):
                        raise SystemExit("auth -bt requires: <bot_token>")
                    args["auth_bot_token"] = remaining[i + 1]
                    i += 2
                    continue
                if item == "-ut":
                    if i + 1 >= len(remaining):
                        raise SystemExit("auth -ut requires: <user_token>")
                    args["auth_user_token"] = remaining[i + 1]
                    i += 2
                    continue
                if item == "-at":
                    if i + 1 >= len(remaining):
                        raise SystemExit("auth -at requires: <app_token>")
                    args["auth_app_token"] = remaining[i + 1]
                    i += 2
                    continue
                if item == "-n":
                    if i + 1 >= len(remaining):
                        raise SystemExit("auth -n requires: <name>")
                    args["auth_name"] = remaining[i + 1]
                    i += 2
                    continue
                raise SystemExit(f"Unknown auth option: {item}")
            return args
        if token == "codex":
            if not remaining or remaining[0] in {"help", "-h"}:
                args["codex_action"] = "help"
                return args
            action = remaining[0]
            rest = remaining[1:]
            if action in {"once", "scan", "service", "ti", "td", "st", "status", "reset-state"}:
                if rest:
                    raise SystemExit(f"Use: slack <preset> codex {action}")
                args["codex_action"] = action
                return args
            if action == "logs":
                if len(rest) > 1:
                    raise SystemExit("Use: slack <preset> codex logs [lines]")
                if rest:
                    args["codex_lines"] = _parse_positive_int(rest[0], "codex logs lines")
                args["codex_action"] = action
                return args
            raise SystemExit("Use: slack <preset> codex once|scan|service|ti|td|st|logs|status|reset-state")
        if token in {"events", "event"}:
            args["command"] = "events"
            if not remaining or remaining[0] in {"help", "-h"}:
                args["events_action"] = "help"
                return args
            action = remaining[0]
            rest = remaining[1:]
            if action in {"once", "sync", "service", "ti", "td", "st", "status", "reset-cache"}:
                if rest:
                    raise SystemExit(f"Use: slack <preset> events {action}")
                args["events_action"] = action
                return args
            if action == "logs":
                if len(rest) > 1:
                    raise SystemExit("Use: slack <preset> events logs [lines]")
                if rest:
                    args["events_lines"] = _parse_positive_int(rest[0], "events logs lines")
                args["events_action"] = action
                return args
            raise SystemExit("Use: slack <preset> events sync|once|service|ti|td|st|logs|status|reset-cache")
        if token in {"post", "dm"}:
            if len(remaining) < 2:
                raise SystemExit(
                    "Use: slack post <contact_label|email|message_id|channel_id> <message> [path...]"
                )
            args["command"] = "post"
            args["recipient"] = remaining[0]
            args["message"] = remaining[1]
            args["paths"] = remaining[2:]
            return args
        if token == "reply":
            if len(remaining) < 2:
                raise SystemExit("Use: slack reply <message_id> <message> [path...]")
            if not parse_message_id(remaining[0]):
                raise SystemExit("Use: slack reply <message_id> <message> [path...]")
            args["recipient"] = remaining[0]
            args["message"] = remaining[1]
            args["paths"] = remaining[2:]
            return args
        if token == "df":
            if len(remaining) < 2 or len(remaining) > 3:
                raise SystemExit("Use: slack df <channel_id> <file_id> [output_path]")
            args["recipient"] = remaining[0]
            args["file_id"] = remaining[1]
            if len(remaining) == 3:
                args["output_path"] = remaining[2]
            return args
        if token == "o":
            if len(remaining) != 1:
                raise SystemExit("Use: slack o <channel_id|message_id>")
            args["recipient"] = remaining[0]
            args["open_mode"] = True
            return args
        if token == "tui":
            if remaining:
                raise SystemExit("Use: slack <preset> tui")
            return args
        if token == "ls":
            if remaining == ["rc"]:
                args["ls_registry"] = True
                return args
            parts = list(remaining)
            positionals = []
            saw_limit = False
            i = 0
            while i < len(parts):
                item = parts[i]
                if item == "-o":
                    args["open_mode"] = True
                    i += 1
                    continue
                if item in ("-ur", "-r"):
                    if args["ls_filter"] != "all":
                        raise SystemExit(_ls_usage())
                    args["ls_filter"] = "unread" if item == "-ur" else "read"
                    i += 1
                    continue
                if item == "-l":
                    if i + 1 >= len(parts):
                        raise SystemExit("ls -l requires: <limit>")
                    if saw_limit:
                        raise SystemExit("ls accepts only one -l <limit>")
                    args["ls_limit"] = _parse_positive_int(parts[i + 1], "ls -l limit")
                    saw_limit = True
                    i += 2
                    continue
                if item == "-f":
                    if i + 1 >= len(parts):
                        raise SystemExit("ls -f requires: <from>")
                    args["ls_from"] = parts[i + 1]
                    i += 2
                    continue
                if item == "-c":
                    if i + 1 >= len(parts):
                        raise SystemExit("ls -c requires: <contains>")
                    args["ls_contains"] = parts[i + 1]
                    i += 2
                    continue
                if item == "-tl":
                    if i + 1 >= len(parts):
                        raise SystemExit("ls -tl requires: <time_limit>")
                    args["ls_time_limit"] = parts[i + 1]
                    i += 2
                    continue
                if item.startswith("-"):
                    raise SystemExit(f"Unknown ls option: {item}")
                positionals.append(item)
                i += 1

            if len(positionals) > 2:
                raise SystemExit(_ls_usage())
            if len(positionals) == 2:
                if saw_limit:
                    raise SystemExit(_ls_usage())
                args["ls_label"] = positionals[0]
                args["ls_limit"] = _parse_positive_int(positionals[1], "ls limit")
            elif len(positionals) == 1:
                if positionals[0].isdigit():
                    if saw_limit:
                        raise SystemExit("ls accepts only one limit")
                    args["ls_limit"] = _parse_positive_int(positionals[0], "ls limit")
                else:
                    args["ls_label"] = positionals[0]
            return args
        if token in {"su", "u"}:
            if not remaining:
                raise SystemExit("Use: slack su <query>")
            query = " ".join(remaining).strip()
            if not query:
                raise SystemExit("Use: slack su <query>")
            args["query"] = query
            return args
        if token == "mra":
            if remaining:
                raise SystemExit("Use: slack mra")
            return args
        if token == "sc":
            if remaining:
                raise SystemExit("Use: slack sc")
            return args
        raise SystemExit(_top_level_usage())

    return args


def list_registered_contacts(contacts):
    if not contacts:
        print("No registered contacts.")
        return

    rows = []
    for label in sorted(contacts):
        rows.append(
            [
                ("label", label),
                ("email", contacts[label]),
            ]
        )
    print_sections(rows)


def _contact_labels_by_target(contacts):
    labels = {}
    for label, target in contacts.items():
        labels.setdefault(target.strip().lower(), []).append(label)
    return labels


def _contact_search_rows(contacts, query):
    needle = query.strip().lower()
    rows = []
    for label, target in sorted(contacts.items()):
        haystack = f"{label} {target}".lower()
        if needle not in haystack:
            continue
        rows.append(
            [
                ("source", "contact"),
                ("label", label),
                ("name", "-"),
                ("email", target),
                ("user_id", target if USER_ID_RE.match(target) else "-"),
            ]
        )
    return rows


def _user_matches_query(user, query):
    needle = query.strip().lower()
    normalized_needle = _normalized_user_name(query)
    profile = user.get("profile") or {}
    fields = [
        user.get("id"),
        user.get("name"),
        profile.get("real_name"),
        profile.get("display_name"),
        profile.get("email"),
    ]
    raw_haystack = " ".join(str(item or "") for item in fields).lower()
    normalized_haystack = _normalized_user_name(raw_haystack)
    return needle in raw_haystack or normalized_needle in normalized_haystack


def _slack_user_rows(token, contacts, query, limit):
    rows = []
    labels_by_target = _contact_labels_by_target(contacts)
    cursor = None
    while True:
        payload = {"limit": "200"}
        if cursor:
            payload["cursor"] = cursor
        data = slack_request("users.list", payload, token, http_method="GET")
        for user in data.get("members") or []:
            if not isinstance(user, dict) or user.get("deleted") or user.get("is_bot"):
                continue
            if not _user_matches_query(user, query):
                continue
            profile = user.get("profile") or {}
            user_id = str(user.get("id") or "-")
            email = str(profile.get("email") or "-")
            labels = sorted(
                set(
                    labels_by_target.get(user_id.lower(), [])
                    + labels_by_target.get(email.lower(), [])
                )
            )
            rows.append(
                [
                    ("source", "user"),
                    ("label", ",".join(labels) if labels else "-"),
                    ("name", _display_user(user, user_id)),
                    ("email", email),
                    ("user_id", user_id),
                ]
            )
            if len(rows) >= limit:
                return rows
        cursor = ((data.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    return rows


def search_users_and_contacts(contacts, token, query, limit=20):
    contact_rows = _contact_search_rows(contacts, query)
    remaining = max(0, limit - len(contact_rows))
    user_rows = _slack_user_rows(token, contacts, query, remaining) if remaining else []
    rows = contact_rows + user_rows
    if not rows:
        print("No users or contacts found.")
        return
    print_sections(rows[:limit])


def read_from_editor():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        temp_path = tmp.name

    try:
        editor_cmd = resolve_editor_cmd()
        try:
            subprocess.run(editor_cmd + [temp_path], check=False)
        except FileNotFoundError:
            raise SystemExit(f"Editor not found: {editor_cmd[0]}")
        with open(temp_path, "r", encoding="utf-8") as handle:
            text = handle.read().strip()

        if not text:
            raise SystemExit("No content; cancelled.")
        return text
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def resolve_editor_cmd():
    editor = os.getenv("VISUAL") or os.getenv("EDITOR") or "vim"
    editor = editor.strip()
    editor_cmd = shlex.split(editor) if editor else ["vim"]
    if not editor_cmd:
        editor_cmd = ["vim"]
    return editor_cmd


def style_help(value):
    return value


def _token_kind(token):
    if token.startswith(APP_TOKEN_PREFIX):
        return "app"
    if token.startswith(BOT_TOKEN_PREFIX):
        return "bot"
    if token.startswith(USER_TOKEN_PREFIXES):
        return "user"
    return "unknown"


def _read_token_file(path):
    expanded = Path(os.path.expandvars(path)).expanduser()
    if not expanded.exists():
        return None
    try:
        token = expanded.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(f"Unable to read Slack token file: {expanded}: {exc}")
    return token or None


def _token_map(config):
    if not isinstance(config, dict):
        return {}
    mapped = {}
    for container_key in ("token", "tokens"):
        payload = config.get(container_key)
        if not isinstance(payload, dict):
            continue
        for kind in ("app", "bot", "user"):
            value = payload.get(kind)
            if isinstance(value, str) and value.strip() and kind not in mapped:
                mapped[kind] = value.strip()
    for key, kind in (
        ("app_token", "app"),
        ("socket_token", "app"),
        ("bot_token", "bot"),
        ("user_token", "user"),
    ):
        value = config.get(key)
        if isinstance(value, str) and value.strip() and kind not in mapped:
            mapped[kind] = value.strip()
    legacy = config.get("token")
    if isinstance(legacy, str) and legacy.strip():
        kind = _token_kind(legacy.strip())
        if kind in {"app", "bot", "user"} and kind not in mapped:
            mapped[kind] = legacy.strip()
    return mapped


def _direct_token(config, keys):
    tokens = _token_map(config)
    key_kinds = {
        "app": "app",
        "app_token": "app",
        "socket_token": "app",
        "bot": "bot",
        "bot_token": "bot",
        "user": "user",
        "user_token": "user",
    }
    for key in keys:
        kind = key_kinds.get(key)
        if kind and tokens.get(kind):
            return tokens[kind]
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _has_token(config, kind):
    return bool(_token_map(config).get(kind))


def _store_account_tokens(account, app_token=None, bot_token=None, user_token=None):
    tokens = _token_map(account)
    if app_token:
        tokens["app"] = app_token
    if bot_token:
        tokens["bot"] = bot_token
    if user_token:
        tokens["user"] = user_token
    account["token"] = {kind: tokens[kind] for kind in ("app", "bot", "user") if tokens.get(kind)}
    for key in ("app_token", "socket_token", "bot_token", "user_token"):
        account.pop(key, None)


def _read_first_config_token(config, keys):
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            token = _read_token_file(value.strip())
            if token:
                return token
    return None


def resolve_token(config=None):
    config = config or {}
    token = _direct_token(config, ("bot_token", "token", "user_token"))
    if not token:
        token = get_env("SLACK_BOT_TOKEN")
    if not token:
        token = get_env("SLACK_TOKEN")
    if not token:
        for key in ("bot_token_file", "token_file", "user_token_file"):
            value = config.get(key)
            if isinstance(value, str) and value.strip():
                token = _read_token_file(value.strip())
                if token:
                    break
    if not token:
        token = _read_token_file(DEFAULT_BOT_TOKEN_FILE)
    if not token:
        token = _read_token_file(DEFAULT_USER_TOKEN_FILE)
    if not token:
        raise SystemExit("Missing Slack token. Set SLACK_BOT_TOKEN or add bot_token_file to slack conf.")
    if _token_kind(token) == "unknown":
        raise SystemExit("Slack token must be a bot token (xoxb-) or user token (xoxp-/xoxc-).")
    return token


def resolve_list_token(config=None):
    config = config or {}
    token = _direct_token(config, ("user_token", "token", "bot_token"))
    if not token:
        token = get_env("SLACK_TOKEN")
    if not token:
        token = _read_first_config_token(config, ("token_file", "user_token_file"))
    if not token:
        token = _read_token_file(DEFAULT_USER_TOKEN_FILE)
    if not token:
        token = get_env("SLACK_BOT_TOKEN")
    if not token:
        token = _read_first_config_token(config, ("bot_token_file",))
    if not token:
        token = _read_token_file(DEFAULT_BOT_TOKEN_FILE)
    if not token:
        raise SystemExit(
            "Missing Slack token. For all-contact ls, add ~/.openclaw/credentials/slack-user-token or set SLACK_TOKEN."
        )
    if _token_kind(token) == "unknown":
        raise SystemExit("Slack token must be a bot token (xoxb-) or user token (xoxp-/xoxc-).")
    return token


def resolve_lookup_token(config=None, fallback_token=None):
    config = config or {}
    token = _direct_token(config, ("user_token", "token"))
    if not token:
        token = get_env("SLACK_TOKEN")
    if not token:
        token = _read_first_config_token(config, ("user_token_file", "token_file"))
    if not token:
        token = _read_token_file(DEFAULT_USER_TOKEN_FILE)
    token = token or fallback_token
    if token and _token_kind(token) == "unknown":
        raise SystemExit("Slack token must be a bot token (xoxb-) or user token (xoxp-/xoxc-).")
    return token


def resolve_direct_post_token(config=None, fallback_token=None):
    token = resolve_lookup_token(config, fallback_token)
    return token or fallback_token


def resolve_app_token(config=None):
    config = config or {}
    token = _direct_token(config, ("app_token", "socket_token"))
    if not token:
        token = get_env("SLACK_APP_TOKEN")
    if not token:
        token = _read_first_config_token(config, ("app_token_file", "socket_token_file"))
    if not token:
        token = _read_token_file(DEFAULT_APP_TOKEN_FILE)
    if not token:
        raise SystemExit(
            "Missing Slack app token. Add accounts.<preset>.app_token with an xapp- token, "
            "or import ~/.openclaw/credentials/slack-app-token with slack auth <preset> -i."
        )
    if _token_kind(token) != "app":
        raise SystemExit("Slack app token must be an app-level token (xapp-).")
    return token


def slack_request(method, payload, token, use_form=False, http_method="POST", allow_error=False):
    requests = _requests()
    url = f"https://slack.com/api/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    response = None
    last_error = None
    for attempt in range(3):
        try:
            if http_method == "GET":
                response = requests.get(
                    url,
                    headers=headers,
                    params=payload,
                    timeout=30,
                )
            elif use_form:
                response = requests.post(
                    url,
                    headers=headers,
                    data=payload,
                    timeout=30,
                )
            else:
                response = requests.post(
                    url,
                    headers={
                        **headers,
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                    timeout=30,
                )
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(2**attempt)
            continue
        if response.status_code != 429 and response.status_code < 500:
            break
        retry_after = response.headers.get("Retry-After")
        delay = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        time.sleep(min(delay, 30))
    if response is None:
        detail = f": {last_error}" if last_error else "."
        raise SystemExit(f"Slack API request failed ({method}){detail}")
    if response.status_code != 200:
        raise SystemExit(
            f"Slack API HTTP {response.status_code}: {response.text.strip()}"
        )
    try:
        data = response.json()
    except json.JSONDecodeError:
        raise SystemExit("Slack API returned invalid JSON.")
    if not data.get("ok") and not allow_error:
        error = data.get("error") or "unknown_error"
        metadata = data.get("response_metadata") or {}
        messages = metadata.get("messages") or []
        if messages:
            raise SystemExit(
                f"Slack API error ({method}): {error} ({'; '.join(messages)})"
            )
        raise SystemExit(f"Slack API error ({method}): {error}")
    return data


def auth_test(token):
    data = slack_request("auth.test", {}, token)
    return data


def _token_bool(value):
    return "yes" if isinstance(value, str) and value.strip() else "no"


def list_account_presets(config):
    accounts = _accounts(config)
    if not accounts:
        print("No account presets configured.")
        return
    rows = []
    for preset in _sorted_presets(accounts):
        account = accounts.get(preset) or {}
        rows.append(
            [
                ("preset", preset),
                ("name", account.get("name") or "-"),
                ("team", account.get("team") or account.get("team_id") or "-"),
                ("bot_token", "yes" if _has_token(account, "bot") else "no"),
                ("user_token", "yes" if _has_token(account, "user") else "no"),
                ("app_token", "yes" if _has_token(account, "app") else "no"),
                ("codex", _token_bool(account.get("codex_session_id"))),
                ("contacts", str(len(normalize_contacts(account)))),
            ]
        )
    print_sections(rows)


def _validate_token_kind(token, expected_kind, label):
    if not token:
        return None
    token = token.strip()
    actual = _token_kind(token)
    if actual != expected_kind:
        raise SystemExit(f"{label} must be a {expected_kind} token.")
    return token


def _import_bot_token(config):
    for candidate in (
        _direct_token(config, ("bot_token",)),
        _read_first_config_token(config, ("bot_token_file",)),
        _read_token_file(DEFAULT_BOT_TOKEN_FILE),
    ):
        if candidate and _token_kind(candidate) == "bot":
            return candidate
    return None


def _import_user_token(config):
    for candidate in (
        _direct_token(config, ("user_token", "token")),
        _read_first_config_token(config, ("user_token_file", "token_file")),
        _read_token_file(DEFAULT_USER_TOKEN_FILE),
    ):
        if candidate and _token_kind(candidate) == "user":
            return candidate
    return None


def _import_app_token(config):
    for candidate in (
        _direct_token(config, ("app_token", "socket_token")),
        _read_first_config_token(config, ("app_token_file", "socket_token_file")),
        _read_token_file(DEFAULT_APP_TOKEN_FILE),
    ):
        if candidate and _token_kind(candidate) == "app":
            return candidate
    return None


def configure_account(config_path, config, preset, bot_token, user_token, app_token, name, import_tokens):
    if not preset:
        raise SystemExit(
            "Use: slack auth <preset> [-i]|[-bt <bot_token>] [-ut <user_token>] [-at <app_token>] [-n <name>]"
        )
    if import_tokens:
        bot_token = bot_token or _import_bot_token(config)
        user_token = user_token or _import_user_token(config)
        app_token = app_token or _import_app_token(config)

    accounts = config.setdefault("accounts", {})
    if not isinstance(accounts, dict):
        raise SystemExit("accounts must be a JSON object.")
    account = accounts.get(preset)
    if account is None:
        account = {}
    if not isinstance(account, dict):
        raise SystemExit(f"accounts['{preset}'] must be a JSON object.")

    bot_token = _validate_token_kind(bot_token, "bot", "bot_token")
    user_token = _validate_token_kind(user_token, "user", "user_token")
    app_token = _validate_token_kind(app_token, "app", "app_token")

    existing_tokens = _token_map(account)
    effective_bot = bot_token or existing_tokens.get("bot")
    effective_user = user_token or existing_tokens.get("user")
    effective_app = app_token or existing_tokens.get("app")
    if not effective_bot and not effective_user and not effective_app:
        raise SystemExit("Provide -bt, -ut, -at, or -i to store at least one Slack token.")
    if not effective_bot and not effective_user:
        raise SystemExit("Provide -bt, -ut, or -i with a bot/user token so Slack auth can be verified.")

    auth_data = auth_test(user_token or bot_token or effective_user or effective_bot)
    if name:
        account["name"] = name.strip()
    _store_account_tokens(
        account,
        app_token=effective_app,
        bot_token=effective_bot,
        user_token=effective_user,
    )
    if "contacts" not in account:
        root_contacts = normalize_contacts(config)
        if root_contacts:
            account["contacts"] = root_contacts
    if "contacts" in config:
        del config["contacts"]
    if "user_labels" in config:
        del config["user_labels"]
    if "defaults" in config:
        del config["defaults"]
    if "default_preset" in config:
        del config["default_preset"]
    for metadata_key in ("team", "team_id", "url", "user_id"):
        account.pop(metadata_key, None)
    accounts[preset] = account

    save_config(config_path, config)
    print(
        "authorized "
        f"preset={preset} "
        f"name={account.get('name') or '-'} "
        f"bot_token={'yes' if _has_token(account, 'bot') else 'no'} "
        f"user_token={'yes' if _has_token(account, 'user') else 'no'} "
        f"app_token={'yes' if _has_token(account, 'app') else 'no'}"
    )


def _resolve_post_target_value(value, token, lookup_token=None, direct_token=None):
    lookup_token = lookup_token or token
    direct_token = direct_token or token
    parsed_message_id = parse_message_id(value)
    if parsed_message_id:
        channel_id, message_ts = parsed_message_id
        return {
            "kind": "message",
            "target": value,
            "channel_id": channel_id,
            "message_ts": message_ts,
        }
    if CONVERSATION_ID_RE.match(value):
        return {
            "kind": "conversation",
            "target": value,
            "channel_id": value,
            "message_ts": None,
        }
    if USER_ID_RE.match(value):
        channel_id = open_dm(value, direct_token)
        return {
            "kind": "user",
            "target": value,
            "channel_id": channel_id,
            "message_ts": None,
            "user_id": value,
        }
    if "@" in value:
        email = value.strip()
        user_id = lookup_user_id_by_email(email, lookup_token)
        channel_id = open_dm(user_id, direct_token)
        return {
            "kind": "email",
            "target": email,
            "channel_id": channel_id,
            "message_ts": None,
            "email": email,
            "user_id": user_id,
        }
    return None


def resolve_post_target(recipient, contacts, token, lookup_token=None, direct_token=None):
    lookup_token = lookup_token or token
    direct_token = direct_token or token
    raw = (recipient or "").strip()
    if not raw:
        raise SystemExit("Post target cannot be empty.")
    if raw in contacts:
        resolved = _resolve_post_target_value(contacts[raw].strip(), token, lookup_token, direct_token)
        if resolved:
            resolved["label"] = raw
            resolved["target"] = raw
            return resolved
        raise SystemExit(f"Saved contact '{raw}' is not an email, user id, channel id, or message id.")
    resolved = _resolve_post_target_value(raw, token, lookup_token, direct_token)
    if resolved:
        return resolved
    raise SystemExit("Post target must be a contact label, email, Slack user id, channel id, or message id.")


def _email_name_query(email):
    local = str(email or "").split("@", 1)[0].split("+", 1)[0]
    return " ".join(re.sub(r"[._-]+", " ", local).split())


def lookup_user_id_by_email(email, token):
    data = slack_request(
        "users.lookupByEmail",
        {"email": email},
        token,
        http_method="GET",
        allow_error=True,
    )
    if data.get("ok") is not True:
        error = data.get("error") or "unknown_error"
        fallback_query = _email_name_query(email)
        if fallback_query:
            user_id = lookup_user_id_by_name(fallback_query, token)
            if user_id:
                return user_id
        if error == "missing_scope":
            raise SystemExit(
                "Slack token is missing users:read.email, and no unique Slack user "
                f"matched '{fallback_query or email}' from contact email {email}."
            )
        raise SystemExit(f"Slack API error (users.lookupByEmail): {error}")
    user = data.get("user") or {}
    user_id = user.get("id")
    if not user_id:
        raise SystemExit("No user found for that email.")
    return user_id


def get_user_info(user_id, token):
    data = slack_request(
        "users.info",
        {"user": user_id},
        token,
        http_method="GET",
    )
    return data.get("user") or {}


def open_dm(user_id, token):
    data = slack_request("conversations.open", {"users": user_id}, token)
    channel = data.get("channel") or {}
    channel_id = channel.get("id")
    if not channel_id:
        raise SystemExit("Unable to open DM channel.")
    return channel_id


def send_post(token, channel_id, text, thread_ts=None):
    payload = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = slack_request(
        "chat.postMessage", payload, token
    )
    message = data.get("message") or {}
    return message.get("ts")


def resolve_reply_thread_ts(channel_id, message_ts, token):
    try:
        message = _hydrate_message(channel_id, message_ts, token)
    except SystemExit:
        message = None
    if not message:
        return message_ts
    return str(message.get("thread_ts") or message_ts)


def expand_existing_path(path, kind):
    expanded = os.path.expanduser(path)
    if kind == "file":
        if not os.path.isfile(expanded):
            raise SystemExit(f"File not found: {path}")
    elif kind == "dir":
        if not os.path.isdir(expanded):
            raise SystemExit(f"Directory not found: {path}")
    return expanded


def zip_directory(dir_path):
    expanded = expand_existing_path(dir_path, "dir")
    base_name = os.path.basename(os.path.normpath(expanded)) or "archive"
    temp_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=f"-{base_name}.zip"
    )
    temp_file.close()
    archive_path = temp_file.name
    try:
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for root, _, files in os.walk(expanded):
                for name in sorted(files):
                    full_path = os.path.join(root, name)
                    rel_path = os.path.relpath(full_path, expanded)
                    archive.write(full_path, arcname=os.path.join(base_name, rel_path))
    except Exception:
        try:
            os.remove(archive_path)
        except OSError:
            pass
        raise
    return archive_path, f"{base_name}.zip"


def _upload_external_file(channel_id, thread_ts, path, filename, token):
    requests = _requests()
    file_size = os.path.getsize(path)
    upload_data = slack_request(
        "files.getUploadURLExternal",
        {"filename": filename, "length": str(file_size)},
        token,
        use_form=True,
    )
    upload_url = upload_data.get("upload_url")
    file_id = upload_data.get("file_id")
    if not upload_url or not file_id:
        raise SystemExit("Slack did not return an upload URL.")

    with open(path, "rb") as handle:
        response = requests.post(
            upload_url,
            data=handle,
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
    if response.status_code not in (200, 201):
        raise SystemExit(
            f"Slack upload HTTP {response.status_code}: {response.text.strip()}"
        )

    payload = {
        "files": [{"id": file_id, "title": filename}],
        "channel_id": channel_id,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    slack_request("files.completeUploadExternal", payload, token)
    return file_id


def send_attachments(channel_id, thread_ts, paths, token):
    uploaded = []
    temporary_archives = []

    try:
        for path in paths:
            expanded = os.path.expanduser(path)
            if os.path.isdir(expanded):
                archive_path, archive_name = zip_directory(path)
                temporary_archives.append(archive_path)
                _upload_external_file(
                    channel_id, thread_ts, archive_path, archive_name, token
                )
                uploaded.append(archive_name)
                continue

            expanded = expand_existing_path(path, "file")
            filename = os.path.basename(expanded)
            _upload_external_file(
                channel_id, thread_ts, expanded, filename, token
            )
            uploaded.append(filename)
    finally:
        for archive_path in temporary_archives:
            try:
                os.remove(archive_path)
            except OSError:
                pass

    return uploaded


def compact_text(value):
    value = (value or "").replace("\n", " ").strip()
    if not value:
        return "-"
    return " ".join(value.split())


def message_text(message):
    primary = (message.get("text") or "").strip()
    if primary:
        return primary

    parts = []
    for attachment in message.get("attachments") or []:
        text = (attachment.get("text") or attachment.get("fallback") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _safe_filename(value, fallback="attachment"):
    text = str(value or "").strip() or fallback
    text = re.sub(r"[\\/:\0]+", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180] or fallback


def _asset_name_from_url(url):
    cleaned = str(url or "").rstrip("/")
    if not cleaned:
        return "embed"
    return cleaned.rsplit("/", 1)[-1] or cleaned


def _add_message_file_asset(collected, seen, file_payload):
    if not isinstance(file_payload, dict):
        return
    file_id = file_payload.get("id")
    download_url = file_payload.get("url_private_download")
    asset_url = (
        download_url
        or file_payload.get("url_private")
        or file_payload.get("permalink")
        or file_payload.get("external_url")
    )
    key = file_id or asset_url or file_payload.get("name") or file_payload.get("title")
    if key and key in seen:
        return
    if key:
        seen.add(key)
    name = file_payload.get("name") or file_payload.get("title") or file_id or "attachment"
    collected.append(
        {
            "kind": "file",
            "id": file_id or "-",
            "name": str(name),
            "download_url": download_url,
            "url": asset_url,
            "payload": file_payload,
        }
    )


def _add_embed_asset(collected, seen, attachment):
    if not isinstance(attachment, dict):
        return
    url = (
        attachment.get("title_link")
        or attachment.get("from_url")
        or attachment.get("original_url")
        or attachment.get("url")
        or attachment.get("image_url")
        or attachment.get("thumb_url")
    )
    title = (
        attachment.get("title")
        or attachment.get("service_name")
        or attachment.get("fallback")
        or _asset_name_from_url(url)
    )
    if not url and not attachment.get("title"):
        return
    key = url or title
    if key and key in seen:
        return
    if key:
        seen.add(key)
    collected.append(
        {
            "kind": "embed",
            "id": "-",
            "name": str(title or "embed"),
            "download_url": None,
            "url": url,
            "text": attachment.get("text") or attachment.get("fallback") or "",
            "payload": attachment,
        }
    )


def message_assets(message):
    collected = []
    seen = set()

    for file_payload in message.get("files") or []:
        _add_message_file_asset(collected, seen, file_payload)

    for attachment in message.get("attachments") or []:
        for file_payload in attachment.get("files") or []:
            _add_message_file_asset(collected, seen, file_payload)
        _add_embed_asset(collected, seen, attachment)

    return collected


def message_files(message):
    return [asset["payload"] for asset in message_assets(message) if asset["kind"] == "file"]


def summarize_attachments(message):
    names = [asset.get("name") or "attachment" for asset in message_assets(message)]
    return ", ".join(names) if names else "-"


def format_ts(ts_value):
    try:
        dt = datetime.fromtimestamp(float(ts_value))
    except (TypeError, ValueError, OSError):
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _month_bounds(year, month):
    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return start, next_month


def _parse_iso_date(value):
    match = _ISO_DATE_RE.match(value)
    if not match:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid ls -tl date '{value}'") from exc


def _parse_iso_month(value):
    match = _ISO_MONTH_RE.match(value)
    if not match:
        return None
    try:
        return _month_bounds(int(match.group("year")), int(match.group("month")))
    except ValueError as exc:
        raise SystemExit(f"Invalid ls -tl month '{value}'") from exc


def _parse_named_month(value):
    match = _NAMED_MONTH_RE.match(value.strip())
    if not match:
        return None
    month = _MONTH_NAMES.get(match.group("month").lower())
    if month is None:
        raise SystemExit(f"Invalid ls -tl month '{value}'")
    return _month_bounds(int(match.group("year")), month)


def _start_ts(value):
    return datetime.combine(value, datetime.min.time()).timestamp()


def _time_window(value):
    if not value:
        return None, None
    expr = value.strip()
    if not expr:
        raise SystemExit("ls -tl requires: <time_limit>")

    if ".." in expr:
        start_raw, _, end_raw = expr.partition("..")
        start = _parse_iso_date(start_raw.strip())
        end = _parse_iso_date(end_raw.strip())
        if start is None or end is None:
            raise SystemExit("ls -tl date ranges must use: YYYY-MM-DD..YYYY-MM-DD")
        if end < start:
            raise SystemExit("ls -tl range end must be on or after start")
        return _start_ts(start), _start_ts(end + timedelta(days=1))

    relative = _RELATIVE_TIME_RE.match(expr)
    if relative:
        amount = int(relative.group("amount"))
        unit = relative.group("unit").lower()
        if amount <= 0:
            raise SystemExit("ls -tl duration must be > 0")
        days_by_unit = {"d": 1, "w": 7, "m": 30, "y": 365}
        return time.time() - (amount * days_by_unit[unit] * 24 * 60 * 60), None

    month_bounds = _parse_iso_month(expr) or _parse_named_month(expr)
    if month_bounds is not None:
        start, next_month = month_bounds
        return _start_ts(start), _start_ts(next_month)

    exact_date = _parse_iso_date(expr)
    if exact_date is not None:
        return _start_ts(exact_date), _start_ts(exact_date + timedelta(days=1))

    raise SystemExit(f"ls -tl supports: {_TIME_LIMIT_SHAPE}")


def message_id(channel_id, ts):
    return f"{channel_id}:{ts}"


def parse_message_id(value):
    if not value or ":" not in value:
        return None
    channel_id, ts = value.split(":", 1)
    if not channel_id or not ts:
        return None
    return channel_id, ts


def extract_ts(payload):
    latest = payload.get("latest")
    if isinstance(latest, dict):
        return latest.get("ts") or "0"
    if isinstance(latest, str):
        return latest
    return "0"


def print_sections(rows):
    for index, row in enumerate(rows, start=1):
        prefix = f"[{index}]"
        print(prefix + ("-" * max(1, 79 - len(prefix))))
        for label, value in row:
            print(f"{label:<8}: {value}")


def list_api(method, params, token):
    cursor = None
    items = []
    while True:
        payload = dict(params)
        if cursor:
            payload["cursor"] = cursor
        data = slack_request(method, payload, token, http_method="GET")
        batch = data.get("channels") or []
        items.extend(batch)
        cursor = ((data.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    return items


def _display_user(user, fallback="-"):
    profile = user.get("profile") or {}
    return (
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("name")
        or fallback
    )


def _user_email(user, fallback="-"):
    profile = user.get("profile") or {}
    return profile.get("email") or fallback


def _conversation_surface(info, channel_id):
    if info.get("is_im") or info.get("user") or str(channel_id).startswith("D"):
        return "dm"
    if info.get("is_mpim"):
        return "group_dm"
    if str(info.get("name") or "").startswith("mpdm-"):
        return "group_dm"
    if info.get("is_channel"):
        return "private_channel" if info.get("is_private") else "channel"
    if str(channel_id).startswith("G"):
        return "private_channel"
    if str(channel_id).startswith("C"):
        return "channel"
    return "conversation"


def _channel_name(channel, channel_id):
    raw_name = (
        channel.get("name")
        or channel.get("name_normalized")
        or channel.get("context_team_name")
        or channel_id
    )
    name = str(raw_name)
    surface = _conversation_surface(channel, channel_id)
    if surface == "group_dm" and name.startswith("mpdm-"):
        stem = re.sub(r"-\d+$", "", name.removeprefix("mpdm-"))
        participants = [part for part in stem.split("--") if part]
        if participants:
            return ", ".join(participants)
    if surface in {"channel", "private_channel"} and name != channel_id and not name.startswith("#"):
        return f"#{name}"
    return name


def _person_conversation_label(user, fallback):
    name = _display_user(user, fallback)
    email = _user_email(user)
    if email != "-" and name != "-":
        return f"{name} <{email}>"
    if email != "-":
        return email
    return name


def _thread_label(message):
    thread_ts = str(message.get("thread_ts") or "")
    ts = str(message.get("ts") or "")
    reply_count = message.get("reply_count")
    if thread_ts and thread_ts != ts:
        return f"reply_to {thread_ts}"
    if thread_ts or reply_count:
        if reply_count:
            return f"root {reply_count} replies"
        return "root"
    return "-"


def _conversation_summary(channel, token, user_cache=None):
    channel_id = channel.get("id") if isinstance(channel, dict) else channel
    if not channel_id:
        return None
    info = slack_request(
        "conversations.info",
        {"channel": channel_id, "include_num_members": "true"},
        token,
        http_method="GET",
    ).get("channel") or {}
    merged = {}
    if isinstance(channel, dict):
        merged.update(channel)
    merged.update(info)
    surface = _conversation_surface(merged, channel_id)
    user_id = merged.get("user")
    user = {}
    if surface == "dm" and user_id:
        if user_cache is not None:
            if user_id not in user_cache:
                user_cache[user_id] = get_user_info(user_id, token)
            user = user_cache[user_id]
        else:
            user = get_user_info(user_id, token)
    email = _user_email(user) if user else "-"
    name = _display_user(user, user_id or "-") if user else _channel_name(merged, channel_id)
    conversation = _person_conversation_label(user, user_id or channel_id) if user else name
    return {
        "label": "-",
        "email": email,
        "name": name,
        "conversation": conversation,
        "surface": surface,
        "members": merged.get("num_members") or "-",
        "user_id": user_id or "-",
        "channel_id": channel_id,
        "info": info,
        "user": user,
    }


def _fallback_conversation_summary(channel_id, channel=None):
    channel = channel if isinstance(channel, dict) else {}
    hint = dict(channel)
    hint.setdefault("id", channel_id)
    return {
        "label": "-",
        "email": "-",
        "name": _channel_name(hint, channel_id),
        "conversation": _channel_name(hint, channel_id),
        "surface": _conversation_surface(hint, channel_id),
        "members": hint.get("num_members") or "-",
        "user_id": hint.get("user") or "-",
        "channel_id": channel_id,
        "info": hint,
        "user": {},
    }


def _dm_info_from_channel(channel, token, user_cache):
    return _conversation_summary(channel, token, user_cache)


def get_all_dm_infos(token):
    im_channels = list_api(
        "users.conversations",
        {"types": "im", "exclude_archived": "true", "limit": "200"},
        token,
    )
    infos = []
    user_cache = {}
    for channel in im_channels:
        info = _dm_info_from_channel(channel, token, user_cache)
        if info:
            infos.append(info)
    return infos


def get_tui_conversation_infos(token):
    channels = list_api(
        "users.conversations",
        {"types": "im,mpim", "exclude_archived": "true", "limit": "200"},
        token,
    )
    infos = []
    user_cache = {}
    for channel in channels:
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        if not channel_id:
            continue
        try:
            info = _conversation_summary(channel, token, user_cache)
        except SystemExit:
            info = _fallback_conversation_summary(channel_id, channel)
        if info and info.get("surface") in {"dm", "group_dm"}:
            infos.append(info)
    return infos


def get_contact_dm_infos(contacts, token):
    im_channels = list_api(
        "users.conversations",
        {"types": "im", "exclude_archived": "true", "limit": "200"},
        token,
    )
    user_to_channel = {}
    for channel in im_channels:
        user_id = channel.get("user")
        channel_id = channel.get("id")
        if user_id and channel_id:
            user_to_channel[user_id] = channel_id

    infos = []
    user_cache = {}
    for label, target in contacts.items():
        email = None
        if USER_ID_RE.match(target):
            user_id = target
        else:
            try:
                user_id = lookup_user_id_by_email(target, token)
            except SystemExit:
                continue
        channel_id = user_to_channel.get(user_id)
        if not channel_id:
            continue
        if user_id not in user_cache:
            user_cache[user_id] = get_user_info(user_id, token)
        user = user_cache[user_id]
        email = _user_email(user, target)
        info = slack_request(
            "conversations.info",
            {"channel": channel_id, "include_num_members": "true"},
            token,
            http_method="GET",
        ).get("channel") or {}
        conversation = _person_conversation_label(user, user_id)
        infos.append(
            {
                "label": label,
                "email": email,
                "name": _display_user(user, user_id),
                "conversation": conversation,
                "surface": "dm",
                "members": info.get("num_members") or "-",
                "user_id": user_id,
                "channel_id": channel_id,
                "info": info,
                "user": user,
            }
        )
    return infos


def get_dm_info(channel_id, token):
    summary = _conversation_summary({"id": channel_id}, token)
    if not summary:
        raise SystemExit(f"Unable to resolve Slack conversation for {channel_id}.")
    return summary


def _download_url_bytes(download_url, token):
    requests = _requests()
    with requests.get(
        download_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
        allow_redirects=True,
    ) as response:
        content_type = response.headers.get("content-type") or ""
        if response.status_code != 200 or "text/html" in content_type:
            raise SystemExit(
                "Downloading files requires a token with file download access, typically files:read."
            )
        return response.content


def _download_file_to_path(download_url, destination, token):
    data = _download_url_bytes(download_url, token)
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(destination, "wb") as handle:
        handle.write(data)


def _snippet_text(file_payload, token):
    download_url = file_payload.get("url_private_download")
    if not download_url:
        return "-"
    data = _download_url_bytes(download_url, token)
    return data.decode("utf-8", errors="replace")


def _download_destination(dm_id, file_payload):
    name = _safe_filename(
        file_payload.get("name") or file_payload.get("title") or file_payload.get("id") or "attachment"
    )
    filename = f"{dm_id}-{file_payload.get('id')}-{name}"
    return os.path.abspath(os.path.expanduser(filename))


def _message_zip_destination(channel_id, ts):
    safe_channel = _safe_filename(channel_id, "conversation")
    safe_ts = _safe_filename(str(ts or "message").replace(".", "-"), "message")
    return os.path.abspath(os.path.expanduser(f"{safe_channel}-{safe_ts}-attachments.zip"))


def _asset_metadata_bytes(asset):
    lines = [
        f"name: {asset.get('name') or '-'}",
        f"kind: {asset.get('kind') or '-'}",
    ]
    if asset.get("url"):
        lines.append(f"url: {asset['url']}")
    if asset.get("text"):
        lines.extend(["", str(asset["text"])])
    return ("\n".join(lines) + "\n").encode("utf-8")


def _asset_bytes(asset, token):
    if asset.get("download_url"):
        return _download_url_bytes(asset["download_url"], token)
    return _asset_metadata_bytes(asset)


def _asset_filename(asset):
    name = _safe_filename(asset.get("name"), "attachment")
    if asset.get("kind") == "embed":
        return name if "." in Path(name).name else f"{name}.url.txt"
    return name


def _unique_arcname(used, filename):
    candidate = filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while candidate in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate


def _message_details(message, dm_id, token):
    downloads = []
    code_blocks = []
    assets = message_assets(message)
    if len(assets) > 1:
        destination = _message_zip_destination(dm_id, message.get("ts"))
        used = set()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for asset in assets:
                arcname = _unique_arcname(used, _asset_filename(asset))
                archive.writestr(arcname, _asset_bytes(asset, token))
                downloads.append(
                    {
                        "id": asset.get("id") or "-",
                        "name": asset.get("name") or "attachment",
                        "kind": asset.get("kind") or "attachment",
                        "path": destination,
                        "zip_entry": arcname,
                    }
                )
    else:
        for asset in assets:
            if asset.get("download_url"):
                destination = _download_destination(dm_id, asset["payload"])
                _download_file_to_path(asset["download_url"], destination, token)
            else:
                destination = os.path.abspath(os.path.expanduser(_asset_filename(asset)))
                parent = os.path.dirname(destination)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(destination, "wb") as handle:
                    handle.write(_asset_metadata_bytes(asset))
            downloads.append(
                {
                    "id": asset.get("id") or "-",
                    "name": asset.get("name") or "attachment",
                    "kind": asset.get("kind") or "attachment",
                    "path": destination,
                }
            )

    for file_payload in message_files(message):
        if file_payload.get("mode") == "snippet":
            code_blocks.append(
                {
                    "id": file_payload.get("id") or "-",
                    "name": file_payload.get("name") or "snippet",
                    "text": _snippet_text(file_payload, token),
                }
            )
    return downloads, code_blocks


def _sender_info(message, token, user_cache):
    user_id = message.get("user")
    if user_id:
        if user_id not in user_cache:
            user_cache[user_id] = get_user_info(user_id, token)
        user = user_cache[user_id]
        name = _display_user(user, user_id)
        email = _user_email(user)
        return {
            "id": user_id,
            "name": name,
            "email": email,
            "label": f"{name} <{email}>" if email != "-" else name,
        }
    bot_profile = message.get("bot_profile") or {}
    bot_id = message.get("bot_id") or bot_profile.get("id") or "-"
    name = bot_profile.get("name") or message.get("username") or bot_id
    return {"id": bot_id, "name": name, "email": "-", "label": name}


def _matches_text(haystack, needle):
    if not needle:
        return True
    return needle.strip().lower() in haystack.strip().lower()


def _search_quote(value):
    cleaned = value.strip()
    if not cleaned:
        return ""
    if re.search(r"\s", cleaned):
        return '"' + cleaned.replace('"', '\\"') + '"'
    return cleaned


def _resolve_filter_user_id(value, contacts, token):
    target = contacts.get(value, value)
    if USER_ID_RE.match(target):
        return target
    if "@" in target:
        try:
            return lookup_user_id_by_email(target, token)
        except SystemExit:
            return None
    return lookup_user_id_by_name(target, token)


def _normalized_user_name(value):
    return " ".join(value.strip().lower().replace(".", " ").split())


def lookup_user_id_by_name(name, token):
    target = _normalized_user_name(name)
    if not target:
        return None
    exact_matches = []
    partial_matches = []
    cursor = None
    while True:
        payload = {"limit": "200"}
        if cursor:
            payload["cursor"] = cursor
        data = slack_request("users.list", payload, token, http_method="GET", allow_error=True)
        if data.get("ok") is not True:
            return None
        for user in data.get("members") or []:
            if not isinstance(user, dict) or user.get("deleted") or user.get("is_bot"):
                continue
            profile = user.get("profile") or {}
            candidates = [
                user.get("name"),
                profile.get("real_name"),
                profile.get("display_name"),
            ]
            normalized = [_normalized_user_name(str(item)) for item in candidates if item]
            if target in normalized:
                exact_matches.append(user)
            elif any(target in item for item in normalized):
                partial_matches.append(user)
        cursor = ((data.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    matches = exact_matches or partial_matches
    if len(matches) == 1:
        return matches[0].get("id")
    return None


def _search_time_terms(time_limit):
    if not time_limit:
        return []
    oldest, latest = _time_window(time_limit)
    terms = []
    if oldest is not None:
        terms.append(f"after:{datetime.fromtimestamp(oldest).strftime('%Y-%m-%d')}")
    if latest is not None:
        terms.append(f"before:{datetime.fromtimestamp(latest).strftime('%Y-%m-%d')}")
    return terms


def _build_search_query(label, contacts, token, sender_filter, contains_filter, time_limit):
    terms = ["is:dm"]
    if label:
        if label not in contacts:
            raise SystemExit(f"Unknown contact label: {label}")
        user_id = _resolve_filter_user_id(contacts[label], contacts, token)
        if user_id:
            terms.append(f"in:<@{user_id}>")
    if sender_filter:
        user_id = _resolve_filter_user_id(sender_filter, contacts, token)
        if user_id:
            terms.append(f"from:<@{user_id}>")
        else:
            terms.append(f"from:{_search_quote(sender_filter)}")
    if contains_filter:
        terms.append(_search_quote(contains_filter))
    terms.extend(_search_time_terms(time_limit))
    return " ".join(term for term in terms if term)


def _hydrate_message(channel_id, ts, token):
    payload = slack_request(
        "conversations.history",
        {
            "channel": channel_id,
            "latest": ts,
            "inclusive": "true",
            "limit": "1",
        },
        token,
        http_method="GET",
    )
    messages = payload.get("messages") or []
    for message in messages:
        if str(message.get("ts") or "") == str(ts):
            return message
    return messages[0] if messages else None


def _entry_passes_filters(entry, filter_mode, sender_filter, contains_filter, time_limit):
    oldest, latest = _time_window(time_limit) if time_limit else (None, None)
    ts_value = entry["sort_ts"]
    if oldest is not None and ts_value < oldest:
        return False
    if latest is not None and ts_value >= latest:
        return False
    if filter_mode == "unread" and not entry.get("unread"):
        return False
    if filter_mode == "read" and entry.get("unread"):
        return False
    sender = entry.get("sender") or {}
    sender_haystack = " ".join(
        str(value)
        for value in (
            sender.get("id"),
            sender.get("name"),
            sender.get("email"),
            entry.get("email"),
        )
        if value
    )
    if not _matches_text(sender_haystack, sender_filter):
        return False
    if not _matches_text(message_text(entry["message"]), contains_filter):
        return False
    return True


def search_dms(
    contacts,
    token,
    limit,
    filter_mode,
    self_user_id,
    open_mode,
    label=None,
    sender_filter=None,
    contains_filter=None,
    time_limit=None,
):
    if _token_kind(token) != "user":
        return None
    query = _build_search_query(label, contacts, token, sender_filter, contains_filter, time_limit)
    payload = slack_request(
        "search.messages",
        {
            "query": query,
            "sort": "timestamp",
            "sort_dir": "desc",
            "count": str(max(20, min(100, limit * 5))),
        },
        token,
        http_method="GET",
        allow_error=True,
    )
    if payload.get("ok") is not True:
        if payload.get("error") in {"not_allowed_token_type", "missing_scope", "no_permission"}:
            return None
        error = payload.get("error") or "unknown_error"
        raise SystemExit(f"Slack API error (search.messages): {error}")

    entries = []
    user_cache = {}
    dm_cache = {}
    for match in (payload.get("messages") or {}).get("matches", []) or []:
        if not isinstance(match, dict):
            continue
        channel = match.get("channel") if isinstance(match.get("channel"), dict) else {}
        channel_id = channel.get("id") or match.get("channel_id")
        ts = str(match.get("ts") or "")
        if not channel_id or not ts:
            continue
        try:
            ts_value = float(ts)
        except ValueError:
            continue
        if channel_id not in dm_cache:
            channel_hint = dict(channel)
            channel_hint.setdefault("id", channel_id)
            try:
                dm_cache[channel_id] = _conversation_summary(channel_hint, token)
            except SystemExit:
                dm_cache[channel_id] = _fallback_conversation_summary(channel_id, channel_hint)
        dm_info = dm_cache[channel_id]
        try:
            hydrated_message = _hydrate_message(channel_id, ts, token)
        except SystemExit:
            hydrated_message = None
        message = hydrated_message or {
            "ts": ts,
            "user": match.get("user"),
            "text": match.get("text") or "",
        }
        sender = _sender_info(message, token, user_cache)
        last_read = (dm_info.get("info") or {}).get("last_read") or "0"
        try:
            last_read_value = float(last_read)
        except (TypeError, ValueError):
            last_read_value = 0.0
        is_self = bool(self_user_id and message.get("user") == self_user_id)
        entry = {
            "sort_ts": ts_value,
            "email": dm_info.get("email") or "-",
            "dm_id": channel_id,
            "channel_id": channel_id,
            "surface": dm_info.get("surface") or "conversation",
            "conversation": dm_info.get("conversation") or dm_info.get("name") or channel_id,
            "members": dm_info.get("members") or "-",
            "message": message,
            "sender": sender,
            "unread": bool(not is_self and ts_value > last_read_value),
        }
        if _entry_passes_filters(entry, filter_mode, sender_filter, contains_filter, time_limit):
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def _print_open_entries(entries, token):
    user_cache = {}
    for index, entry in enumerate(entries, start=1):
        prefix = f"[{index}]"
        sender = entry.get("sender") or _sender_info(entry["message"], token, user_cache)
        channel_id = entry.get("channel_id") or entry.get("dm_id")
        members = entry.get("members") or "-"
        thread = _thread_label(entry["message"])
        print(prefix + ("-" * max(1, 79 - len(prefix))))
        print(f"{'message_id':<10}: {message_id(channel_id, entry['message'].get('ts'))}")
        print(f"{'surface':<12}: {entry.get('surface') or 'conversation'}")
        print(f"{'conversation':<12}: {entry.get('conversation') or entry.get('email') or channel_id}")
        print(f"{'channel_id':<12}: {channel_id}")
        if members != "-":
            print(f"{'members':<12}: {members}")
        if thread != "-":
            print(f"{'thread':<12}: {thread}")
        print(f"{'date':<8}: {format_ts(entry['message'].get('ts'))}")
        print(f"{'from':<8}: {sender['label']}")
        text = message_text(entry["message"]).rstrip()
        print(style_help(f"{'text':<8}: {text if text else '-'}"))

        downloads, code_blocks = _message_details(entry["message"], channel_id, token)
        if downloads:
            zip_paths = sorted({item["path"] for item in downloads if item.get("zip_entry")})
            for zip_path in zip_paths:
                print(style_help(f"{'zip':<8}: {zip_path}"))
            for file_info in downloads:
                detail = file_info.get("zip_entry") or file_info["path"]
                print(
                    style_help(
                        f"{file_info.get('kind') or 'file':<8}: {file_info['id']} {file_info['name']} {detail}"
                    )
                )
        else:
            print(style_help(f"{'asset':<8}: -"))

        if code_blocks:
            for block in code_blocks:
                print(style_help(f"{'code':<8}: {block['id']} {block['name']}"))
                print(style_help(block["text"]))
        else:
            print(style_help(f"{'code':<8}: -"))


def _collect_messages(
    contact_dm,
    token,
    limit,
    filter_mode,
    self_user_id,
    sender_filter=None,
    contains_filter=None,
    time_limit=None,
):
    entries = []
    info_channel = contact_dm["info"]
    last_read = info_channel.get("last_read") or "0"
    try:
        last_read_value = float(last_read)
    except (TypeError, ValueError):
        last_read_value = 0.0

    oldest, latest = _time_window(time_limit) if time_limit else (None, None)
    user_cache = {}
    cursor = None
    matched = 0
    while True:
        history_params = {
            "channel": contact_dm["channel_id"],
            "limit": str(max(20, min(100, limit * 3))),
            **({"cursor": cursor} if cursor else {}),
        }
        if oldest is not None:
            history_params["oldest"] = f"{oldest:.6f}"
            history_params["inclusive"] = "true"
        if latest is not None:
            history_params["latest"] = f"{latest:.6f}"
            history_params["inclusive"] = "true"
        history = slack_request(
            "conversations.history",
            history_params,
            token,
            http_method="GET",
        )
        messages = history.get("messages") or []
        for message in messages:
            ts = message.get("ts")
            if not ts:
                continue
            try:
                ts_value = float(ts)
            except (TypeError, ValueError):
                continue
            sender = _sender_info(message, token, user_cache)
            is_self = bool(self_user_id and message.get("user") == self_user_id)
            is_unread = bool(not is_self and ts_value > last_read_value)
            if filter_mode == "unread" and not is_unread:
                continue
            if filter_mode == "read" and is_unread:
                continue
            sender_haystack = " ".join(
                str(value)
                for value in (
                    sender.get("id"),
                    sender.get("name"),
                    sender.get("email"),
                    contact_dm.get("label"),
                    contact_dm.get("email"),
                    contact_dm.get("name"),
                )
                if value
            )
            if not _matches_text(sender_haystack, sender_filter):
                continue
            if not _matches_text(message_text(message), contains_filter):
                continue

            entries.append(
                {
                    "sort_ts": ts_value,
                    "email": contact_dm["email"],
                    "dm_id": contact_dm["channel_id"],
                    "channel_id": contact_dm["channel_id"],
                    "surface": contact_dm.get("surface") or "dm",
                    "conversation": contact_dm.get("conversation")
                    or contact_dm.get("name")
                    or contact_dm["email"],
                    "members": contact_dm.get("members") or "-",
                    "message": message,
                    "sender": sender,
                    "unread": is_unread,
                }
            )
            matched += 1
            if matched >= limit:
                break

        if matched >= limit:
            break

        cursor = (
            (history.get("response_metadata") or {}).get("next_cursor") or ""
        ).strip()
        if not cursor:
            break

    return entries


def _empty_dm_message(filter_mode):
    if filter_mode == "unread":
        return "No unread DMs."
    if filter_mode == "read":
        return "No read DMs."
    return "No DMs."


def _list_entry_fields(item):
    channel_id = item.get("channel_id") or item.get("dm_id")
    fields = [
        ("message_id", message_id(channel_id, item["message"].get("ts"))),
        ("surface", item.get("surface") or "conversation"),
        ("conversation", item.get("conversation") or item.get("email") or channel_id),
        ("channel_id", channel_id),
    ]
    members = item.get("members") or "-"
    if members != "-":
        fields.append(("members", members))
    thread = _thread_label(item["message"])
    if thread != "-":
        fields.append(("thread", thread))
    fields.extend(
        [
            ("date", format_ts(item["message"].get("ts"))),
            ("from", item["sender"]["label"]),
            ("text", compact_text(message_text(item["message"]))),
            ("attachments", summarize_attachments(item["message"])),
        ]
    )
    return fields


def list_dms(
    contacts,
    token,
    limit,
    filter_mode,
    self_user_id,
    open_mode,
    label=None,
    sender_filter=None,
    contains_filter=None,
    time_limit=None,
    cache_path=None,
):
    entries = _event_cache_search_entries(
        cache_path,
        contacts,
        limit,
        filter_mode,
        self_user_id,
        label=label,
        sender_filter=sender_filter,
        contains_filter=contains_filter,
        time_limit=time_limit,
    )
    if not entries:
        entries = search_dms(
            contacts,
            token,
            limit,
            filter_mode,
            self_user_id,
            open_mode,
            label=label,
            sender_filter=sender_filter,
            contains_filter=contains_filter,
            time_limit=time_limit,
        )
    if entries is None:
        entries = []
        if label:
            if label not in contacts:
                raise SystemExit(f"Unknown contact label: {label}")
            dm_infos = get_contact_dm_infos({label: contacts[label]}, token)
        else:
            dm_infos = get_all_dm_infos(token)

        for contact_dm in dm_infos:
            entries.extend(
                _collect_messages(
                    contact_dm,
                    token,
                    limit,
                    filter_mode,
                    self_user_id,
                    sender_filter,
                    contains_filter,
                    time_limit,
                )
            )
    else:
        entries = list(entries)

    if not entries:
        print(_empty_dm_message(filter_mode))
        return

    entries.sort(key=lambda item: item["sort_ts"], reverse=True)
    selected = entries[:limit]
    selected.sort(key=lambda item: item["sort_ts"])

    if open_mode:
        _print_open_entries(selected, token)
        latest_by_channel = {}
        for item in selected:
            ts = item["message"].get("ts")
            if not ts:
                continue
            channel_id = item.get("channel_id") or item.get("dm_id")
            current = latest_by_channel.get(channel_id)
            if current is None or float(ts) > float(current):
                latest_by_channel[channel_id] = ts
        marked = 0
        for channel_id, ts in latest_by_channel.items():
            slack_request(
                "conversations.mark",
                {"channel": channel_id, "ts": ts},
                token,
                use_form=True,
            )
            _event_cache_mark_read(cache_path, channel_id, ts)
            marked += 1
        print(f"ls_opened messages={len(selected)} marked_conversations={marked}")
        return

    print_sections([_list_entry_fields(item) for item in selected])


TUI_RECENT_MESSAGE_LIMIT = 100
TUI_HYDRATE_WORKERS = 8
TUI_LATEST_MESSAGE_CURSOR = -1
ERZA_CHAT_SOURCE_PATHS = (
    "~/.erza/app/src",
    "~/Infra/erza/app/src",
)
TUI_SHORTCUT_LINES = [
    ("j / k", "move down / up"),
    ("ctrl+n / p", "next / previous message"),
    ("l / enter", "open conversation, file picker, or selected file"),
    ("h", "back or close modal"),
    ("i", "enter insert mode"),
    ("ctrl+a/e", "composer start / end"),
    ("ctrl+b/f", "composer previous / next character"),
    ("alt+b/f", "composer previous / next word"),
    ("ctrl+w/h", "delete previous word / character"),
    ("ctrl+d/k/u", "delete next char / to end / full input"),
    ("esc", "return to normal mode or close modal"),
    ("r", "refresh"),
    ("g / gg / G", "jump top / bottom"),
    ("?", "toggle shortcuts"),
    ("q", "quit"),
]
CTRL_A = 1
CTRL_B = 2
CTRL_D = 4
CTRL_E = 5
CTRL_F = 6
CTRL_H = 8
CTRL_K = 11
CTRL_N = 14
CTRL_P = 16
CTRL_U = 21
CTRL_W = 23
TUI_ALT_B = -1001
TUI_ALT_F = -1002
TUI_INSERT_ESCAPE_SEQUENCE_TIMEOUT_MS = 25


def _load_erza_chat_api():
    for candidate in ERZA_CHAT_SOURCE_PATHS:
        path = os.path.abspath(os.path.expanduser(candidate))
        if os.path.exists(os.path.join(path, "erza", "chat.py")) and path not in sys.path:
            sys.path.insert(0, path)
    try:
        from erza.chat import (
            ChatCallbacks,
            ChatConversation,
            ChatEmbed,
            ChatFile,
            ChatMessage,
            run_chat_app,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    return {
        "ChatCallbacks": ChatCallbacks,
        "ChatConversation": ChatConversation,
        "ChatEmbed": ChatEmbed,
        "ChatFile": ChatFile,
        "ChatMessage": ChatMessage,
        "run_chat_app": run_chat_app,
    }


def _clip(value, width):
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _safe_addstr(window, y, x, text, attr=0):
    try:
        height, width = window.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        window.addnstr(y, x, str(text), max(0, width - x - 1), attr)
    except Exception:
        return


def _safe_move(window, y, x):
    try:
        height, width = window.getmaxyx()
        if y < 0 or y >= height:
            return
        window.move(y, max(0, min(x, width - 1)))
    except Exception:
        return


def _tui_summary_from_search_match(
    channel_id,
    channel,
    sender,
    self_user_id,
    token=None,
    user_cache=None,
):
    info = _fallback_conversation_summary(channel_id, channel)
    surface = info.get("surface")
    if surface not in {"dm", "group_dm"}:
        surface = "dm" if str(channel_id).startswith("D") else "group_dm"
        info["surface"] = surface
    info.setdefault("info", {})
    info["info"].setdefault("last_read", str(time.time()))
    if surface == "dm":
        partner_id = _tui_partner_user_id(channel)
        if partner_id and token:
            if user_cache is not None:
                if partner_id not in user_cache:
                    user_cache[partner_id] = _tui_fetch_user_info(partner_id, token)
                user = user_cache[partner_id]
            else:
                user = _tui_fetch_user_info(partner_id, token)
            if user:
                info["conversation"] = _display_user(user, partner_id)
                info["name"] = _display_user(user, partner_id)
                info["email"] = _user_email(user)
                info["user_id"] = partner_id
                info["user"] = user
                return info
        if sender.get("id") != self_user_id:
            if sender.get("label") and sender["label"] != "-":
                info["conversation"] = sender["label"]
            if sender.get("name") and sender["name"] != "-":
                info["name"] = sender["name"]
            if sender.get("email") and sender["email"] != "-":
                info["email"] = sender["email"]
            if sender.get("id") and sender["id"] != "-":
                info["user_id"] = sender["id"]
    if surface == "group_dm" and info.get("conversation") == channel_id:
        name = channel.get("name") or channel.get("name_normalized")
        info["conversation"] = _channel_name({"id": channel_id, "name": name or channel_id}, channel_id)
    return info


def _tui_entry_from_message(message, info, sender, self_user_id):
    ts = str(message.get("ts") or "")
    try:
        ts_value = float(ts)
    except ValueError:
        ts_value = 0.0
    last_read = (info.get("info") or {}).get("last_read") or "0"
    try:
        last_read_value = float(last_read)
    except (TypeError, ValueError):
        last_read_value = 0.0
    is_self = bool(self_user_id and message.get("user") == self_user_id)
    return {
        "sort_ts": ts_value,
        "email": info.get("email") or "-",
        "dm_id": info["channel_id"],
        "channel_id": info["channel_id"],
        "surface": info.get("surface") or "dm",
        "conversation": info.get("conversation") or info.get("name") or info["channel_id"],
        "user_id": info.get("user_id") or "-",
        "members": info.get("members") or "-",
        "message": message,
        "sender": sender,
        "unread": bool(not is_self and ts_value > last_read_value),
    }


def _tui_sender_from_search_match(match, message):
    user_id = message.get("user") or match.get("user") or "-"
    username = (
        match.get("username")
        or message.get("username")
        or (message.get("bot_profile") or {}).get("name")
        or user_id
    )
    return {
        "id": user_id,
        "name": username,
        "email": "-",
        "label": username,
    }


def _tui_search_recent_matches(token, limit):
    if _token_kind(token) != "user":
        raise SystemExit("slack tui requires a user token with search:read.")
    payload = slack_request(
        "search.messages",
        {
            "query": "is:dm",
            "sort": "timestamp",
            "sort_dir": "desc",
            "count": str(max(20, min(100, limit))),
        },
        token,
        http_method="GET",
        allow_error=True,
    )
    if payload.get("ok") is not True:
        error = payload.get("error") or "unknown_error"
        if error in {"not_allowed_token_type", "missing_scope", "no_permission"}:
            raise SystemExit(_tui_scope_error())
        raise SystemExit(f"Slack API error (search.messages): {error}")
    matches = (payload.get("messages") or {}).get("matches", []) or []
    return [match for match in matches if isinstance(match, dict)][:limit]


def _tui_scope_error():
    return (
        "slack tui requires user token scopes: search:read, users:read, "
        "im:read, im:history, im:write, mpim:read, mpim:history, mpim:write."
    )


def _tui_partner_user_id(channel):
    if not isinstance(channel, dict):
        return None
    channel_id = channel.get("id")
    if _conversation_surface(channel, channel_id) != "dm":
        return None
    partner_id = channel.get("user")
    channel_name = str(channel.get("name") or channel.get("name_normalized") or "")
    if not partner_id and USER_ID_RE.match(channel_name):
        partner_id = channel_name
    return partner_id


def _tui_fetch_user_info(user_id, token):
    payload = slack_request(
        "users.info",
        {"user": user_id},
        token,
        http_method="GET",
        allow_error=True,
    )
    if payload.get("ok") is not True:
        error = payload.get("error") or "unknown_error"
        if error in {"not_allowed_token_type", "missing_scope", "no_permission"}:
            raise SystemExit(_tui_scope_error())
        return {}
    return payload.get("user") or {}


def _tui_prefetch_dm_users(channel_hints, token, user_cache):
    partner_ids = []
    for channel_id, channel in channel_hints.items():
        hint = dict(channel)
        hint.setdefault("id", channel_id)
        partner_id = _tui_partner_user_id(hint)
        if partner_id and partner_id not in user_cache:
            partner_ids.append(partner_id)
    partner_ids = sorted(set(partner_ids))
    if not partner_ids:
        return
    worker_count = min(TUI_HYDRATE_WORKERS, len(partner_ids))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_tui_fetch_user_info, partner_id, token): partner_id
            for partner_id in partner_ids
        }
        for future in as_completed(futures):
            partner_id = futures[future]
            user = future.result()
            if user:
                user_cache[partner_id] = user


def _tui_fetch_conversation_info(channel_id, channel_hint, token):
    data = slack_request(
        "conversations.info",
        {"channel": channel_id, "include_num_members": "true"},
        token,
        http_method="GET",
        allow_error=True,
    )
    merged = dict(channel_hint or {})
    merged.setdefault("id", channel_id)
    if data.get("ok") is not True:
        return merged
    info = data.get("channel") or {}
    if isinstance(info, dict):
        merged.update(info)
        merged.setdefault("id", channel_id)
    return merged


def _tui_hydrate_conversation_hints(channel_hints, token):
    if not channel_hints:
        return {}
    hydrated = {channel_id: dict(channel or {}) for channel_id, channel in channel_hints.items()}
    worker_count = min(TUI_HYDRATE_WORKERS, len(channel_hints))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_tui_fetch_conversation_info, channel_id, channel, token): channel_id
            for channel_id, channel in channel_hints.items()
        }
        for future in as_completed(futures):
            channel_id = futures[future]
            hydrated[channel_id] = future.result()
    return hydrated


def _tui_fetch_history_messages(channel_id, token):
    history = slack_request(
        "conversations.history",
        {
            "channel": channel_id,
            "limit": str(TUI_RECENT_MESSAGE_LIMIT),
        },
        token,
        http_method="GET",
        allow_error=True,
    )
    if history.get("ok") is not True:
        error = history.get("error") or "unknown_error"
        if error in {"not_allowed_token_type", "missing_scope", "no_permission"}:
            raise SystemExit(_tui_scope_error())
        return []
    return history.get("messages") or []


def _tui_recent_channel_groups(matches):
    wanted_by_channel = {}
    channel_hints = {}
    for match in matches:
        channel = match.get("channel") if isinstance(match.get("channel"), dict) else {}
        channel_id = channel.get("id") or match.get("channel_id")
        ts = str(match.get("ts") or "")
        if not channel_id or not ts:
            continue
        wanted_by_channel.setdefault(channel_id, set()).add(ts)
        hint = channel_hints.setdefault(channel_id, dict(channel))
        hint.setdefault("id", channel_id)
    return wanted_by_channel, channel_hints


def _tui_hydrate_recent_messages(matches, token):
    wanted_by_channel, channel_hints = _tui_recent_channel_groups(matches)
    hydrated = {}
    if not wanted_by_channel:
        return hydrated, channel_hints
    worker_count = min(TUI_HYDRATE_WORKERS, len(wanted_by_channel))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_tui_fetch_history_messages, channel_id, token): channel_id
            for channel_id in wanted_by_channel
        }
        for future in as_completed(futures):
            channel_id = futures[future]
            messages = future.result()
            wanted_ts_values = wanted_by_channel[channel_id]
            for message in messages:
                ts = str(message.get("ts") or "")
                if ts in wanted_ts_values:
                    hydrated[(channel_id, ts)] = message
    return hydrated, channel_hints


def _tui_load_recent_entries(token, self_user_id, limit=TUI_RECENT_MESSAGE_LIMIT, hydrate=False):
    matches = _tui_search_recent_matches(token, limit)
    if hydrate:
        hydrated, channel_hints = _tui_hydrate_recent_messages(matches, token)
    else:
        _wanted_by_channel, channel_hints = _tui_recent_channel_groups(matches)
        hydrated = {}
    channel_hints = _tui_hydrate_conversation_hints(channel_hints, token)
    dm_cache = {}
    user_cache = {}
    _tui_prefetch_dm_users(channel_hints, token, user_cache)
    entries = []
    for match in matches:
        channel_id = (
            (match.get("channel") or {}).get("id")
            if isinstance(match.get("channel"), dict)
            else None
        )
        channel_id = channel_id or match.get("channel_id")
        ts = str(match.get("ts") or "")
        if not channel_id or not ts:
            continue
        message = hydrated.get((channel_id, ts)) or {
            "ts": ts,
            "user": match.get("user"),
            "username": match.get("username"),
            "text": match.get("text") or "",
        }
        sender = _tui_sender_from_search_match(match, message)
        if channel_id not in dm_cache:
            channel_hint = dict(channel_hints.get(channel_id) or {})
            channel_hint.setdefault("id", channel_id)
            dm_cache[channel_id] = _tui_summary_from_search_match(
                channel_id,
                channel_hint,
                sender,
                self_user_id,
                token,
                user_cache,
            )
        info = dm_cache[channel_id]
        if info.get("surface") not in {"dm", "group_dm"}:
            continue
        entries.append(_tui_entry_from_message(message, info, sender, self_user_id))
        if len(entries) >= limit:
            break
    entries.sort(key=lambda item: item["sort_ts"], reverse=True)
    return entries


def _tui_conversation_rows_from_entries(entries, history_loaded=False):
    grouped = {}
    for entry in entries:
        channel_id = entry.get("channel_id") or entry.get("dm_id")
        if not channel_id:
            continue
        row = grouped.setdefault(
            channel_id,
            {
                "info": {
                    "channel_id": channel_id,
                    "surface": entry.get("surface") or "dm",
                    "conversation": entry.get("conversation") or channel_id,
                    "name": entry.get("conversation") or channel_id,
                    "members": entry.get("members") or "-",
                    "email": entry.get("email") or "-",
                    "user_id": entry.get("user_id") or "-",
                },
                "latest": entry,
                "messages": [],
                "sort_ts": entry["sort_ts"],
                "unread_ts": entry["sort_ts"] if entry.get("unread") else 0,
                "history_loaded": history_loaded,
            },
        )
        row["messages"].append(entry)
        if entry.get("unread") and entry["sort_ts"] > row.get("unread_ts", 0):
            row["unread_ts"] = entry["sort_ts"]
        if entry["sort_ts"] > row["sort_ts"]:
            row["sort_ts"] = entry["sort_ts"]
            row["latest"] = entry
    rows = list(grouped.values())
    for row in rows:
        row["messages"].sort(key=lambda item: item["sort_ts"])
    rows.sort(
        key=lambda item: (
            1 if item.get("unread_ts") else 0,
            item.get("unread_ts") or 0,
            item.get("sort_ts") or 0,
        ),
        reverse=True,
    )
    return rows


def _tui_load_conversations(token, self_user_id, limit=TUI_RECENT_MESSAGE_LIMIT, cache_path=None):
    if cache_path:
        cached_rows = _event_cache_load_conversation_rows(cache_path, self_user_id, limit)
        if cached_rows:
            return cached_rows
    return _tui_conversation_rows_from_entries(
        _tui_load_recent_entries(token, self_user_id, limit, hydrate=False),
        history_loaded=False,
    )


def _tui_entries_from_history(conversation_row, token, self_user_id, limit=TUI_RECENT_MESSAGE_LIMIT):
    if not conversation_row:
        return []
    info = conversation_row.get("info") or {}
    channel_id = info.get("channel_id")
    if not channel_id:
        return []
    history = slack_request(
        "conversations.history",
        {
            "channel": channel_id,
            "limit": str(max(1, min(TUI_RECENT_MESSAGE_LIMIT, limit or TUI_RECENT_MESSAGE_LIMIT))),
        },
        token,
        http_method="GET",
        allow_error=True,
    )
    if history.get("ok") is not True:
        error = history.get("error") or "unknown_error"
        if error in {"not_allowed_token_type", "missing_scope", "no_permission"}:
            raise SystemExit(_tui_scope_error())
        raise SystemExit(f"Slack API error (conversations.history): {error}")

    user_cache = {}
    entries = []
    for message in history.get("messages") or []:
        if not isinstance(message, dict):
            continue
        sender = _sender_info(message, token, user_cache)
        entries.append(_tui_entry_from_message(message, info, sender, self_user_id))
    entries.sort(key=lambda item: item["sort_ts"])
    return entries


def _tui_load_messages(conversation_row, token=None, self_user_id=None, limit=None, force=False, cache_path=None):
    if not conversation_row:
        return []
    cache_channel_id = ((conversation_row.get("info") or {}).get("channel_id") or "")
    if cache_path and cache_channel_id and conversation_row.get("history_loaded"):
        cached_entries = _event_cache_load_channel_entries(
            cache_path,
            cache_channel_id,
            self_user_id,
            limit or TUI_RECENT_MESSAGE_LIMIT,
        )
        if cached_entries:
            conversation_row["messages"] = cached_entries
            conversation_row["latest"] = cached_entries[-1]
            conversation_row["sort_ts"] = cached_entries[-1]["sort_ts"]
            return cached_entries
    if token and self_user_id and (force or not conversation_row.get("history_loaded")):
        entries = _tui_entries_from_history(
            conversation_row,
            token,
            self_user_id,
            limit or TUI_RECENT_MESSAGE_LIMIT,
        )
        conversation_row["messages"] = entries
        conversation_row["history_loaded"] = True
        if entries:
            conversation_row["latest"] = entries[-1]
            conversation_row["sort_ts"] = entries[-1]["sort_ts"]
        if cache_path:
            _event_cache_store_conversation_row(cache_path, conversation_row, history_loaded=True)
    entries = list(conversation_row.get("messages") or [])
    entries.sort(key=lambda item: item["sort_ts"])
    return entries


def _event_cache_paths(account, preset):
    slug = _safe_preset_slug(preset)
    base = _state_base_dir()
    return {
        "db_file": _expand_path(account.get("events_cache_db") or account.get("event_cache_db") or str(base / f"events-{slug}.db")),
        "log_file": _expand_path(account.get("events_log_file") or str(base / f"events-{slug}.log")),
    }


def _event_cache_db_path(account, preset):
    return _event_cache_paths(account, preset)["db_file"]


def _json_dumps(value):
    return json.dumps(value if isinstance(value, (dict, list)) else {}, sort_keys=True, separators=(",", ":"))


def _json_loads(value, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _ts_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _event_cache_connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _event_cache_init(conn)
    return conn


def _event_cache_init(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            channel_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            conversation TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            members TEXT NOT NULL,
            user_id TEXT NOT NULL,
            last_read TEXT NOT NULL,
            info_json TEXT NOT NULL,
            latest_ts REAL NOT NULL DEFAULT 0,
            unread_ts REAL NOT NULL DEFAULT 0,
            history_loaded INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            sort_ts REAL NOT NULL,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            unread INTEGER NOT NULL DEFAULT 0,
            sender_json TEXT NOT NULL,
            message_json TEXT NOT NULL,
            event_id TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_channel_ts ON messages(channel_id, sort_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_sort_ts ON messages(sort_ts DESC);
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cache_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO cache_state(key, value) VALUES(?, ?)",
        ("schema_version", str(EVENT_CACHE_SCHEMA_VERSION)),
    )
    conn.commit()


def _event_cache_now():
    return datetime.now().astimezone().isoformat()


def _event_cache_get_state(conn, key, default=""):
    row = conn.execute("SELECT value FROM cache_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _event_cache_set_state(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO cache_state(key, value) VALUES(?, ?)", (key, str(value)))


def _event_cache_claim_event(conn, event_id, channel_id, ts):
    if not event_id:
        return True
    try:
        conn.execute(
            "INSERT INTO events(event_id, channel_id, ts, received_at) VALUES(?, ?, ?, ?)",
            (event_id, channel_id or "", ts or "", _event_cache_now()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def _event_cache_conversation_info_from_entry(entry):
    return {
        "channel_id": entry.get("channel_id") or entry.get("dm_id") or "-",
        "surface": entry.get("surface") or "dm",
        "conversation": entry.get("conversation") or entry.get("email") or entry.get("channel_id") or "-",
        "name": entry.get("conversation") or entry.get("email") or entry.get("channel_id") or "-",
        "email": entry.get("email") or "-",
        "members": entry.get("members") or "-",
        "user_id": entry.get("user_id") or (entry.get("sender") or {}).get("id") or "-",
        "info": {"last_read": "0"},
    }


def _event_cache_merge_info(existing, incoming):
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value not in (None, "", "-"):
            merged[key] = value
    nested = dict((existing or {}).get("info") or {})
    nested.update((incoming or {}).get("info") or {})
    if nested:
        merged["info"] = nested
    return merged


def _event_cache_upsert_conversation(conn, info, *, latest_ts=0.0, unread_ts=0.0, history_loaded=False):
    info = dict(info or {})
    channel_id = info.get("channel_id") or info.get("id")
    if not channel_id:
        return
    existing_row = conn.execute(
        "SELECT * FROM conversations WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    existing_info = _json_loads(existing_row["info_json"], {}) if existing_row else {}
    info = _event_cache_merge_info(existing_info, info)
    surface = info.get("surface") or _conversation_surface(info, channel_id)
    conversation = info.get("conversation") or info.get("name") or channel_id
    name = info.get("name") or conversation
    email = info.get("email") or "-"
    members = str(info.get("members") or "-")
    user_id = info.get("user_id") or "-"
    last_read = str(info.get("last_read") or (info.get("info") or {}).get("last_read") or "0")
    if existing_row:
        latest_ts = max(float(existing_row["latest_ts"] or 0), float(latest_ts or 0))
        unread_ts = max(float(existing_row["unread_ts"] or 0), float(unread_ts or 0))
        history_loaded = bool(history_loaded or existing_row["history_loaded"])
    conn.execute(
        """
        INSERT OR REPLACE INTO conversations(
            channel_id, surface, conversation, name, email, members, user_id,
            last_read, info_json, latest_ts, unread_ts, history_loaded, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_id,
            surface,
            conversation,
            name,
            email,
            members,
            user_id,
            last_read,
            _json_dumps(info),
            float(latest_ts or 0),
            float(unread_ts or 0),
            1 if history_loaded else 0,
            _event_cache_now(),
        ),
    )


def _event_cache_upsert_entry(conn, entry, *, event_id=None, history_loaded=False):
    message = entry.get("message") or {}
    channel_id = entry.get("channel_id") or entry.get("dm_id")
    ts = str(message.get("ts") or "")
    if not channel_id or not ts:
        return False
    sort_ts = float(entry.get("sort_ts") or _ts_float(ts))
    if event_id and not _event_cache_claim_event(conn, event_id, channel_id, ts):
        return False
    unread_ts = sort_ts if entry.get("unread") else 0.0
    info = _event_cache_conversation_info_from_entry(entry)
    _event_cache_upsert_conversation(
        conn,
        info,
        latest_ts=sort_ts,
        unread_ts=unread_ts,
        history_loaded=history_loaded,
    )
    sender = entry.get("sender") or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO messages(
            message_id, channel_id, ts, sort_ts, user_id, text, unread,
            sender_json, message_json, event_id, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id(channel_id, ts),
            channel_id,
            ts,
            sort_ts,
            str(message.get("user") or sender.get("id") or "-"),
            message_text(message),
            1 if entry.get("unread") else 0,
            _json_dumps(sender),
            _json_dumps(message),
            event_id or "",
            _event_cache_now(),
        ),
    )
    return True


def _event_cache_store_conversation_row(cache_path, row, *, history_loaded=None):
    if not cache_path or not row:
        return 0
    with _event_cache_connect(cache_path) as conn:
        info = dict(row.get("info") or {})
        channel_id = info.get("channel_id")
        latest_ts = float(row.get("sort_ts") or 0)
        unread_ts = float(row.get("unread_ts") or 0)
        _event_cache_upsert_conversation(
            conn,
            info,
            latest_ts=latest_ts,
            unread_ts=unread_ts,
            history_loaded=bool(row.get("history_loaded")) if history_loaded is None else bool(history_loaded),
        )
        count = 0
        for entry in row.get("messages") or []:
            if channel_id and not entry.get("channel_id"):
                entry = {**entry, "channel_id": channel_id, "dm_id": channel_id}
            if _event_cache_upsert_entry(conn, entry, history_loaded=bool(history_loaded)):
                count += 1
        conn.commit()
        return count


def _event_cache_store_entries(cache_path, entries, *, event_id=None, history_loaded=False):
    if not cache_path:
        return 0
    with _event_cache_connect(cache_path) as conn:
        count = 0
        for entry in entries or []:
            if _event_cache_upsert_entry(conn, entry, event_id=event_id, history_loaded=history_loaded):
                count += 1
        conn.commit()
        return count


def _event_cache_entry_from_row(row, self_user_id=None):
    info = _json_loads(row["info_json"], {})
    channel_id = row["channel_id"]
    info.setdefault("channel_id", channel_id)
    info.setdefault("surface", row["conversation_surface"])
    info.setdefault("conversation", row["conversation_label"])
    info.setdefault("name", row["conversation_name"])
    info.setdefault("email", row["conversation_email"])
    info.setdefault("members", row["conversation_members"])
    info.setdefault("user_id", row["conversation_user_id"])
    message = _json_loads(row["message_json"], {})
    sender = _json_loads(row["sender_json"], {})
    unread = bool(row["unread"])
    if self_user_id and message.get("user") == self_user_id:
        unread = False
    return {
        "sort_ts": float(row["sort_ts"] or 0),
        "email": info.get("email") or "-",
        "dm_id": channel_id,
        "channel_id": channel_id,
        "surface": info.get("surface") or "dm",
        "conversation": info.get("conversation") or info.get("name") or channel_id,
        "user_id": info.get("user_id") or "-",
        "members": info.get("members") or "-",
        "message": message,
        "sender": sender,
        "unread": unread,
    }


def _event_cache_load_entries(cache_path, self_user_id=None, limit=100, channel_id=None):
    path = Path(cache_path) if cache_path else None
    if not path or not path.exists():
        return []
    query = """
        SELECT messages.*, conversations.info_json, conversations.surface,
               conversations.surface AS conversation_surface,
               conversations.conversation AS conversation_label,
               conversations.name AS conversation_name,
               conversations.email AS conversation_email,
               conversations.members AS conversation_members,
               conversations.user_id AS conversation_user_id
        FROM messages
        JOIN conversations ON conversations.channel_id = messages.channel_id
    """
    params = []
    if channel_id:
        query += " WHERE messages.channel_id = ?"
        params.append(channel_id)
    query += " ORDER BY messages.sort_ts DESC LIMIT ?"
    params.append(max(1, int(limit or 100)))
    with _event_cache_connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
    entries = [_event_cache_entry_from_row(row, self_user_id) for row in rows]
    entries.sort(key=lambda item: item["sort_ts"])
    return entries


def _event_cache_history_loaded_map(cache_path):
    path = Path(cache_path) if cache_path else None
    if not path or not path.exists():
        return {}
    with _event_cache_connect(path) as conn:
        rows = conn.execute("SELECT channel_id, history_loaded FROM conversations").fetchall()
    return {row["channel_id"]: bool(row["history_loaded"]) for row in rows}


def _event_cache_load_conversation_rows(cache_path, self_user_id, limit=TUI_RECENT_MESSAGE_LIMIT):
    entries = _event_cache_load_entries(cache_path, self_user_id, max(TUI_RECENT_MESSAGE_LIMIT * 5, limit * 5))
    if not entries:
        return []
    rows = _tui_conversation_rows_from_entries(entries, history_loaded=False)
    history_loaded = _event_cache_history_loaded_map(cache_path)
    for row in rows:
        channel_id = (row.get("info") or {}).get("channel_id")
        row["history_loaded"] = bool(history_loaded.get(channel_id))
    return rows[:limit]


def _event_cache_load_channel_entries(cache_path, channel_id, self_user_id=None, limit=TUI_RECENT_MESSAGE_LIMIT):
    return _event_cache_load_entries(cache_path, self_user_id, limit, channel_id=channel_id)


def _event_cache_mark_read(cache_path, channel_id, latest_ts):
    if not cache_path or not channel_id or not latest_ts:
        return
    with _event_cache_connect(cache_path) as conn:
        conn.execute(
            "UPDATE conversations SET unread_ts = 0, last_read = ?, updated_at = ? WHERE channel_id = ?",
            (str(latest_ts), _event_cache_now(), channel_id),
        )
        conn.execute(
            "UPDATE messages SET unread = 0, updated_at = ? WHERE channel_id = ? AND sort_ts <= ?",
            (_event_cache_now(), channel_id, _ts_float(latest_ts)),
        )
        conn.commit()


def _event_cache_label_matches(entry, contacts, label):
    if not label:
        return True
    if label not in contacts:
        raise SystemExit(f"Unknown contact label: {label}")
    target = str(contacts[label] or "").strip().lower()
    sender = entry.get("sender") or {}
    haystack = " ".join(
        str(value)
        for value in (
            label,
            target,
            entry.get("conversation"),
            entry.get("email"),
            entry.get("user_id"),
            sender.get("id"),
            sender.get("name"),
            sender.get("email"),
            sender.get("label"),
        )
        if value
    ).lower()
    return bool(target and target in haystack) or label.lower() in haystack


def _event_cache_search_entries(
    cache_path,
    contacts,
    limit,
    filter_mode,
    self_user_id,
    label=None,
    sender_filter=None,
    contains_filter=None,
    time_limit=None,
):
    entries = _event_cache_load_entries(cache_path, self_user_id, max(200, limit * 20))
    if not entries:
        return []
    selected = []
    for entry in sorted(entries, key=lambda item: item["sort_ts"], reverse=True):
        if not _event_cache_label_matches(entry, contacts, label):
            continue
        if not _entry_passes_filters(entry, filter_mode, sender_filter, contains_filter, time_limit):
            continue
        selected.append(entry)
        if len(selected) >= limit:
            break
    selected.sort(key=lambda item: item["sort_ts"])
    return selected


def _tui_selected_rendered_row(state):
    rows = state.get("rendered_rows") or []
    if not rows:
        return None
    index = max(0, min(int(state.get("cursor_row") or 0), len(rows) - 1))
    state["cursor_row"] = index
    return rows[index]


def _tui_selected_file_row(state):
    row = _tui_selected_rendered_row(state)
    if not row or row.get("kind") != "file_button":
        return None
    return row


def _tui_first_file_row_index(rows):
    for index, row in enumerate(rows or []):
        if row.get("kind") == "file_button":
            return index
    return None


def _tui_message_start_row_indices(rows):
    return [index for index, row in enumerate(rows or []) if row.get("message_start")]


def _tui_first_message_row_index(rows):
    starts = _tui_message_start_row_indices(rows)
    return starts[0] if starts else None


def _tui_last_message_row_index(rows):
    starts = _tui_message_start_row_indices(rows)
    return starts[-1] if starts else None


def _tui_move_cursor_row(state, delta):
    rows = state.get("rendered_rows") or []
    if not rows:
        return
    state["cursor_row"] = max(0, min(len(rows) - 1, int(state.get("cursor_row") or 0) + delta))
    state["stick_bottom"] = False


def _tui_move_message_row(state, delta):
    rows = state.get("rendered_rows") or []
    starts = _tui_message_start_row_indices(rows)
    if not starts:
        return
    cursor = max(0, min(int(state.get("cursor_row") or 0), len(rows) - 1))
    if delta > 0:
        target = next((index for index in starts if index > cursor), starts[-1])
    else:
        previous = [index for index in starts if index < cursor]
        target = previous[-1] if previous else starts[0]
    state["cursor_row"] = target
    state["stick_bottom"] = False


def _tui_focus_latest_message(state):
    state["cursor_row"] = TUI_LATEST_MESSAGE_CURSOR


def _tui_open_file_modal_for_row(state, row):
    if not row or row.get("kind") != "file_button":
        state["status"] = "select a file button"
        return False
    assets = [asset for asset in row.get("assets") or [] if asset.get("kind") == "file"]
    if not assets:
        state["status"] = "no files"
        return False
    state["modal"] = {
        "kind": "files",
        "entry": row.get("entry"),
        "assets": assets,
        "index": 0,
        "scroll": 0,
    }
    state["status"] = f"{len(assets)} files"
    return True


def _tui_selected_modal_asset(state):
    modal = state.get("modal") or {}
    assets = modal.get("assets") or []
    if not assets:
        return None
    index = max(0, min(int(modal.get("index") or 0), len(assets) - 1))
    modal["index"] = index
    return assets[index]


def _tui_move_file_modal(state, delta):
    modal = state.get("modal") or {}
    assets = modal.get("assets") or []
    if not assets:
        return
    modal["index"] = max(0, min(len(assets) - 1, int(modal.get("index") or 0) + delta))


def _tui_download_asset_open_path(entry, asset, token):
    if not entry or not asset:
        return None
    channel_id = entry.get("channel_id") or entry.get("dm_id") or "conversation"
    message = entry.get("message") or {}
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    if asset.get("kind") == "file" and payload:
        destination = _download_destination(channel_id, payload)
    else:
        safe_channel = _safe_filename(channel_id, "conversation")
        safe_ts = _safe_filename(str(message.get("ts") or "message").replace(".", "-"), "message")
        destination = os.path.abspath(
            os.path.expanduser(f"{safe_channel}-{safe_ts}-{_asset_filename(asset)}")
        )
    data = _asset_bytes(asset, token)
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(destination, "wb") as handle:
        handle.write(data)
    return destination


def _open_path(path):
    command, wait = _resolve_file_open_command(path)
    try:
        if wait:
            return subprocess.run(command, check=False).returncode
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return 0
    except FileNotFoundError:
        raise SystemExit(f"File opener not found: {command[0]}")


def _resolve_file_open_command(path):
    mime_type, _encoding = mimetypes.guess_type(path)
    if mime_type == "application/pdf":
        command = _first_available_open_command(
            os.getenv("SLACK_PDF_VIEWER"),
            os.getenv("ERZA_PDF_VIEWER"),
            "zathura",
            "evince",
            "xdg-open",
        )
        if command:
            return _expand_file_open_command(command, path), False
    if mime_type and mime_type.startswith("image/"):
        command = _first_available_open_command(
            os.getenv("SLACK_IMAGE_VIEWER"),
            os.getenv("ERZA_IMAGE_VIEWER"),
            "swayimg",
            "imv",
            "feh",
            "xdg-open",
        )
        if command:
            return _expand_file_open_command(command, path), False
    return resolve_editor_cmd() + [path], True


def _first_available_open_command(*commands):
    for raw_command in commands:
        command = shlex.split(raw_command or "")
        if command and shutil.which(command[0]):
            return command
    return None


def _expand_file_open_command(command, path):
    if any("{file}" in token for token in command):
        return [token.replace("{file}", path) for token in command]
    return list(command) + [path]


def _tui_status_for_entry(entry):
    if not entry:
        return "No messages."
    channel_id = entry.get("channel_id") or entry.get("dm_id")
    return f"{message_id(channel_id, entry['message'].get('ts'))}  {format_ts(entry['message'].get('ts'))}"


def _tui_conversation_line(row, width):
    info = row.get("info") or {}
    latest = row.get("latest") or {}
    entry = latest if isinstance(latest, dict) else {}
    surface = "gdm" if info.get("surface") == "group_dm" else "dm"
    unread = "*" if row.get("unread_ts") else " "
    date = format_ts((entry.get("message") or {}).get("ts")) if entry else "-"
    label = info.get("conversation") or info.get("name") or info.get("channel_id") or "-"
    return _clip(f"{unread} {surface:<3} {label}  {date}", width)


def _tui_message_line(entry, width):
    sender = (entry.get("sender") or {}).get("label") or "-"
    marker = "*" if entry.get("unread") else " "
    attachments = summarize_attachments(entry.get("message") or {})
    attach_label = f" [{attachments}]" if attachments != "-" else ""
    text = compact_text(message_text(entry.get("message") or {}))
    return _clip(f"{marker} {format_ts(entry['message'].get('ts'))}  {sender}: {text}{attach_label}", width)


def _tui_sender_name(sender):
    sender = sender or {}
    name = str(sender.get("name") or "").strip()
    if name and name != "-":
        return name
    label = str(sender.get("label") or "-").strip()
    label = re.sub(r"\s*<[^>]+>\s*$", "", label).strip()
    return label or "-"


def _tui_selected_conversation(state):
    conversations = state.get("conversations") or []
    if not conversations:
        return None
    index = max(0, min(int(state.get("conversation_index") or 0), len(conversations) - 1))
    state["conversation_index"] = index
    return conversations[index]


def _tui_conversation_label(row):
    info = (row or {}).get("info") or {}
    return info.get("conversation") or info.get("name") or info.get("channel_id") or "-"


def _tui_box_top(title, inner_width):
    title_text = _clip(f"[ {title} ]", max(1, inner_width))
    return "+-" + title_text + "-" * max(inner_width + 1 - len(title_text), 0) + "+"


def _tui_box_bottom(box_width):
    return "+" + "-" * max(0, box_width - 2) + "+"


def _tui_box_content(value, inner_width):
    return f"| {str(value or '')[:inner_width]:<{inner_width}} |"


def _tui_file_button_label(count):
    return f"<<<{count} Files>>>"


def _tui_nested_file_box_rows(assets, inner_width):
    count = len(assets or [])
    button = _tui_file_button_label(count)
    title = "[ Files ]"
    nested_inner = max(12, min(inner_width - 4, max(len(button), len(title)) + 2))
    nested_width = nested_inner + 4
    top = "+-" + title + "-" * max(nested_inner + 1 - len(title), 0) + "+"
    bottom = "+" + "-" * max(0, nested_width - 2) + "+"
    content = f"| {button:<{nested_inner}} |"
    return top, content, bottom


def _tui_embed_box_rows(embed, inner_width):
    title = "[ Embed ]"
    name = str(embed.get("name") or "embed").strip()
    url = str(embed.get("url") or "").strip()
    text = str(embed.get("text") or "").strip()
    raw_lines = [item for item in (name, url, text) if item]
    if not raw_lines:
        raw_lines = ["embed"]
    nested_inner = max(12, min(inner_width - 4, max(len(title), *(len(item) for item in raw_lines)) + 2))
    rows = ["+-" + title + "-" * max(nested_inner + 1 - len(title), 0) + "+"]
    for raw_line in raw_lines:
        for line in textwrap.wrap(raw_line, nested_inner) or [""]:
            rows.append(f"| {line:<{nested_inner}} |")
    rows.append("+" + "-" * max(0, nested_inner + 2) + "+")
    return rows


def _tui_render_message_rows(messages, width):
    rows = []
    box_width = max(24, width)
    inner_width = max(10, box_width - 4)
    for entry in messages:
        message = entry.get("message") or {}
        sender = _tui_sender_name(entry.get("sender") or {})
        stamp = format_ts(message.get("ts"))
        assets = message_assets(message)
        file_assets = [asset for asset in assets if asset.get("kind") == "file"]
        embed_assets = [asset for asset in assets if asset.get("kind") == "embed"]
        rows.append(
            {
                "text": _tui_box_top(f"{sender}  {stamp}", inner_width),
                "entry": entry,
                "kind": "message_box",
                "message_start": True,
            }
        )
        text = (message.get("text") or "").strip() or "-"
        for paragraph in str(text).splitlines() or [""]:
            wrapped = textwrap.wrap(paragraph, inner_width) or [""]
            rows.extend(
                {
                    "text": _tui_box_content(line, inner_width),
                    "entry": entry,
                    "kind": "message_text",
                }
                for line in wrapped
            )
        for embed in embed_assets:
            for line in _tui_embed_box_rows(embed, inner_width):
                rows.append({"text": _tui_box_content(line, inner_width), "entry": entry, "kind": "embed_box"})
        if file_assets:
            top, button, bottom = _tui_nested_file_box_rows(file_assets, inner_width)
            rows.append({"text": _tui_box_content(top, inner_width), "entry": entry, "kind": "file_box"})
            rows.append(
                {
                    "text": _tui_box_content(button, inner_width),
                    "entry": entry,
                    "assets": file_assets,
                    "file_index": 0,
                    "kind": "file_button",
                }
            )
            rows.append({"text": _tui_box_content(bottom, inner_width), "entry": entry, "kind": "file_box"})
        rows.append({"text": _tui_box_bottom(box_width), "entry": entry, "kind": "message_box"})
        rows.append({"text": "", "entry": None, "kind": "spacer"})
    if rows:
        rows = rows[:-1]
    else:
        rows = [{"text": "No messages.", "entry": None, "kind": "empty"}]
    return rows


def _tui_render_message_lines(messages, width):
    return [row["text"] for row in _tui_render_message_rows(messages, width)]


def _tui_transcript_status(messages, rendered_line_count, view_height, scroll):
    message_count = len(messages or [])
    if not message_count:
        return "0 messages"
    rendered_line_count = max(1, int(rendered_line_count or 1))
    view_height = max(1, int(view_height or 1))
    scroll = max(0, min(int(scroll or 0), max(0, rendered_line_count - view_height)))
    visible_end = min(rendered_line_count, scroll + view_height)
    return f"{message_count} messages  lines {scroll + 1}-{visible_end}/{rendered_line_count}"


def _tui_adjust_scroll(index, scroll, height, length):
    if height <= 0:
        return 0
    index = max(0, min(index, max(0, length - 1)))
    if index < scroll:
        return index
    if index >= scroll + height:
        return index - height + 1
    return max(0, min(scroll, max(0, length - height)))


def _tui_shortcut_modal_lines(inner_width):
    lines = []
    for label, description in TUI_SHORTCUT_LINES:
        wrapped = textwrap.wrap(description, width=max(10, inner_width - 15)) or [description]
        for index, part in enumerate(wrapped):
            prefix = f"{label:<13} " if index == 0 else " " * 14
            lines.append(_clip(prefix + part, inner_width))
    return lines


def _tui_draw_modal_box(stdscr, title, lines, *, selected_index=None, item_offset=0, body_height=None):
    height, width = stdscr.getmaxyx()
    if height < 6 or width < 24:
        return
    inner_width = min(max(24, max((len(line) for line in lines), default=0)), max(24, width - 8))
    box_width = inner_width + 4
    max_body_height = max(1, height - 4)
    if body_height is None:
        effective_body_height = min(len(lines), max_body_height)
    else:
        effective_body_height = min(max(1, int(body_height)), max_body_height)
    body_lines = list(lines[:effective_body_height])
    if len(body_lines) < effective_body_height:
        body_lines.extend([""] * (effective_body_height - len(body_lines)))
    box_height = len(body_lines) + 2
    y = max(0, (height - box_height) // 2)
    x = max(0, (width - box_width) // 2)
    top = _tui_box_top(title, inner_width)
    bottom = _tui_box_bottom(box_width)
    _safe_addstr(stdscr, y, x, top)
    for index, line in enumerate(body_lines, start=1):
        screen_y = y + index
        _safe_addstr(stdscr, screen_y, x, "| ")
        _safe_addstr(stdscr, screen_y, x + 2, " " * inner_width)
        _safe_addstr(stdscr, screen_y, x + box_width - 2, " |")
        _safe_addstr(stdscr, screen_y, x + 2, _clip(line, inner_width))
        if selected_index is not None and item_offset + index - 1 == selected_index:
            _safe_addstr(stdscr, screen_y, max(0, x - 2), ">")
    _safe_addstr(stdscr, y + box_height - 1, x, bottom)


def _tui_draw_shortcuts_modal(stdscr):
    height, width = stdscr.getmaxyx()
    inner_width = min(64, max(24, width - 8))
    _tui_draw_modal_box(stdscr, "Shortcuts", _tui_shortcut_modal_lines(inner_width))


def _tui_draw_file_modal(stdscr, state):
    modal = state.get("modal") or {}
    if modal.get("kind") != "files":
        return
    assets = modal.get("assets") or []
    if not assets:
        return
    height, _width = stdscr.getmaxyx()
    list_height = min(7, max(1, height - 4))
    index = max(0, min(int(modal.get("index") or 0), len(assets) - 1))
    modal["index"] = index
    modal["scroll"] = _tui_adjust_scroll(
        index,
        int(modal.get("scroll") or 0),
        list_height,
        len(assets),
    )
    scroll = int(modal["scroll"])
    visible_assets = assets[scroll : scroll + list_height]
    lines = [
        f"{item.get('kind') or 'file'}  {item.get('name') or 'attachment'}"
        for item in visible_assets
    ]
    _tui_draw_modal_box(
        stdscr,
        f"{len(assets)} Files",
        lines,
        selected_index=index,
        item_offset=scroll,
        body_height=7,
    )


def _tui_draw_conversations(stdscr, state, height, width):
    title = f"slack tui  conversations  {state.get('status') or ''}".strip()
    _safe_addstr(stdscr, 0, 0, _clip(title, width - 1))
    _safe_addstr(stdscr, 1, 0, "-" * max(0, width - 1))
    conversations = state.get("conversations") or []
    conv_index = int(state.get("conversation_index") or 0)
    conv_rows = max(1, height - 2)
    state["conversation_scroll"] = _tui_adjust_scroll(
        conv_index,
        int(state.get("conversation_scroll") or 0),
        conv_rows,
        len(conversations),
    )
    for row_offset in range(conv_rows):
        idx = int(state["conversation_scroll"]) + row_offset
        if idx >= len(conversations):
            break
        if idx == conv_index:
            _safe_addstr(stdscr, 2 + row_offset, 0, ">")
        _safe_addstr(
            stdscr,
            2 + row_offset,
            2,
            _tui_conversation_line(conversations[idx], width - 3),
        )


def _tui_draw_conversation(stdscr, state, height, width):
    selected_conv = _tui_selected_conversation(state)
    label = _tui_conversation_label(selected_conv)
    status = state.get("status") or ""
    messages = state.get("messages") or []
    input_active = bool(state.get("input_active", False))
    rendered = _tui_render_message_rows(messages, width - 3)
    message_height = max(1, height - 4)
    state["message_view_height"] = message_height
    state["rendered_rows"] = rendered
    max_scroll = max(0, len(rendered) - message_height)
    if input_active and state.get("stick_bottom", True):
        scroll = max_scroll
        state["cursor_row"] = max(0, len(rendered) - 1)
    elif not input_active:
        raw_cursor_row = state.get("cursor_row", TUI_LATEST_MESSAGE_CURSOR)
        try:
            cursor_row_value = int(raw_cursor_row)
        except (TypeError, ValueError):
            cursor_row_value = TUI_LATEST_MESSAGE_CURSOR
        if cursor_row_value == TUI_LATEST_MESSAGE_CURSOR:
            latest_message_index = _tui_last_message_row_index(rendered)
            cursor_row = latest_message_index if latest_message_index is not None else max(0, len(rendered) - 1)
            scroll = max_scroll
        else:
            cursor_row = max(0, min(cursor_row_value, max(0, len(rendered) - 1)))
            scroll = _tui_adjust_scroll(
                cursor_row,
                int(state.get("message_scroll") or 0),
                message_height,
                len(rendered),
            )
        state["cursor_row"] = cursor_row
    else:
        scroll = max(0, min(int(state.get("message_scroll") or 0), max_scroll))
    state["message_scroll"] = scroll
    state["rendered_line_count"] = len(rendered)
    transcript_status = _tui_transcript_status(messages, len(rendered), message_height, scroll)
    title = f"slack tui  {label}  {transcript_status}  {status}".strip()
    _safe_addstr(stdscr, 0, 0, _clip(title, width - 1))
    _safe_addstr(stdscr, 1, 0, "-" * max(0, width - 1))
    _safe_addstr(stdscr, height - 2, 0, "-" * max(0, width - 1))
    for row_offset in range(message_height):
        idx = int(state["message_scroll"]) + row_offset
        if idx >= len(rendered):
            break
        row = rendered[idx]
        if not input_active and idx == state.get("cursor_row"):
            _safe_addstr(stdscr, 2 + row_offset, 0, ">")
        _safe_addstr(stdscr, 2 + row_offset, 2, _clip(row["text"], width - 3))

    composer = state.get("composer") or ""
    prompt_width = max(1, width - 1)
    if input_active:
        visible_prompt, cursor_col = _tui_composer_prompt_view(
            composer,
            state.get("composer_cursor") or 0,
            prompt_width,
        )
    else:
        visible_prompt, cursor_col = "[normal]", 0
    _safe_addstr(stdscr, height - 1, 0, _clip(visible_prompt, prompt_width))
    if input_active:
        _safe_move(stdscr, height - 1, cursor_col)


def _tui_draw(stdscr, state):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < 10 or width < 50:
        _safe_addstr(stdscr, 0, 0, "Terminal too small for slack tui.")
        stdscr.refresh()
        return
    if state.get("mode") == "conversation":
        _tui_draw_conversation(stdscr, state, height, width)
    else:
        _tui_draw_conversations(stdscr, state, height, width)
    if state.get("modal"):
        _tui_draw_file_modal(stdscr, state)
    if state.get("show_help"):
        _tui_draw_shortcuts_modal(stdscr)
    stdscr.refresh()


def _tui_refresh_messages(state, token, self_user_id, keep_latest=True, force=False, cache_path=None):
    conversations = state.get("conversations") or []
    if not conversations:
        state["messages"] = []
        state["message_index"] = 0
        state["message_scroll"] = 0
        return
    conv_index = max(0, min(int(state.get("conversation_index") or 0), len(conversations) - 1))
    state["conversation_index"] = conv_index
    messages = _tui_load_messages(conversations[conv_index], token, self_user_id, force=force, cache_path=cache_path)
    state["messages"] = messages
    state["message_index"] = max(0, len(messages) - 1) if keep_latest else 0
    state["message_scroll"] = 0
    state["stick_bottom"] = True
    state["cursor_row"] = max(0, int(state.get("rendered_line_count") or 1) - 1)


def _tui_latest_message_ts(messages):
    latest = None
    for entry in messages or []:
        ts = str((entry.get("message") or {}).get("ts") or "")
        if not ts:
            continue
        try:
            ts_value = float(ts)
        except (TypeError, ValueError):
            continue
        if latest is None or ts_value > latest[0]:
            latest = (ts_value, ts)
    return latest[1] if latest else None


def _tui_conversation_latest_ts(conversation_row):
    if not conversation_row:
        return None
    entries = list(conversation_row.get("messages") or [])
    latest = conversation_row.get("latest")
    if isinstance(latest, dict):
        entries.append(latest)
    ts = _tui_latest_message_ts(entries)
    if ts:
        return ts
    sort_ts = conversation_row.get("sort_ts")
    try:
        return f"{float(sort_ts):.6f}" if sort_ts else None
    except (TypeError, ValueError):
        return None


def _tui_clear_conversation_unread(conversation_row, messages, latest_ts):
    if conversation_row is None:
        return
    for entry in messages or []:
        entry["unread"] = False
    conversation_row["unread_ts"] = 0
    if messages:
        latest = max(messages, key=lambda item: item.get("sort_ts") or 0)
        conversation_row["latest"] = latest
        conversation_row["sort_ts"] = latest.get("sort_ts") or conversation_row.get("sort_ts") or 0
    info = conversation_row.get("info") or {}
    if latest_ts:
        info["last_read"] = latest_ts
        nested = info.get("info")
        if isinstance(nested, dict):
            nested["last_read"] = latest_ts


def _tui_mark_conversation_row_read(conversation_row, token, cache_path=None):
    channel_id = ((conversation_row or {}).get("info") or {}).get("channel_id")
    latest_ts = _tui_conversation_latest_ts(conversation_row)
    if not conversation_row or not channel_id or not latest_ts:
        return False, "missing_ts"
    data = slack_request(
        "conversations.mark",
        {"channel": channel_id, "ts": latest_ts},
        token,
        use_form=True,
        allow_error=True,
    )
    if data.get("ok") is not True:
        conversation_row["mark_read_error"] = _tui_mark_read_error(conversation_row, data)
        return False, conversation_row["mark_read_error"]
    conversation_row.pop("mark_read_error", None)
    _tui_clear_conversation_unread(conversation_row, conversation_row.get("messages") or [], latest_ts)
    _event_cache_mark_read(cache_path, channel_id, latest_ts)
    return True, ""


def _tui_mark_selected_conversation_read(state, token):
    selected = _tui_selected_conversation(state)
    messages = state.get("messages") or []
    if not selected:
        return False
    selected["messages"] = messages
    marked, _error = _tui_mark_conversation_row_read(selected, token)
    return marked


def _tui_mark_all_conversations_read(state, token, cache_path=None):
    conversations = state.get("conversations") or []
    marked = 0
    failed = []
    for row in conversations:
        if not bool(row.get("unread_ts")):
            continue
        marked_read, error = _tui_mark_conversation_row_read(row, token, cache_path=cache_path)
        if marked_read:
            marked += 1
        else:
            failed.append(error)
    if failed:
        state["status"] = f"marked_read={marked} failed={len(failed)} {failed[0]}"
        return False
    for entry in state.get("messages") or []:
        entry["unread"] = False
    state["status"] = f"marked_read={marked}"
    return True


def _tui_apply_leader_key(state, key, token, cache_path=None):
    if key == 27:
        state["leader_buffer"] = ""
        return False
    leader = state.get("leader_buffer") or ""
    if not leader:
        if key == ord(","):
            state["leader_buffer"] = ","
            state["status"] = ","
            return True
        return False
    if not 32 <= key <= 126:
        state["leader_buffer"] = ""
        return True
    sequence = leader + chr(key)
    if not ",mra".startswith(sequence):
        state["leader_buffer"] = ""
        state["status"] = ""
        return True
    state["leader_buffer"] = sequence
    state["status"] = sequence
    if sequence == ",mra":
        state["leader_buffer"] = ""
        _tui_mark_all_conversations_read(state, token, cache_path=cache_path)
    return True


def _tui_mark_read_error(conversation_row, data):
    error = data.get("error") or "unknown_error"
    if error == "missing_scope":
        surface = ((conversation_row or {}).get("info") or {}).get("surface")
        if surface == "group_dm":
            return "missing_scope:add mpim:write to user token"
        if surface == "dm":
            return "missing_scope:add im:write to user token"
    return error


def _tui_refresh(state, token, self_user_id, cache_path=None):
    previous_channel = None
    conversations = state.get("conversations") or []
    if conversations:
        selected = conversations[max(0, min(int(state.get("conversation_index") or 0), len(conversations) - 1))]
        previous_channel = (selected.get("info") or {}).get("channel_id")
    state["status"] = "loading..."
    conversations = _tui_load_conversations(token, self_user_id, cache_path=cache_path)
    state["conversations"] = conversations
    state["conversation_index"] = 0
    if previous_channel:
        for index, row in enumerate(conversations):
            if (row.get("info") or {}).get("channel_id") == previous_channel:
                state["conversation_index"] = index
                break
    state["conversation_scroll"] = 0
    message_count = sum(len(row.get("messages") or []) for row in conversations)
    state["status"] = f"{message_count} recent messages / {len(conversations)} conversations"


def _tui_hydrate_selected_conversation_label(row, token):
    info = (row or {}).get("info") or {}
    if info.get("surface") != "dm":
        return
    user_id = info.get("user_id")
    if not user_id or user_id == "-" or not USER_ID_RE.match(user_id):
        return
    user = _tui_fetch_user_info(user_id, token)
    if not user:
        return
    info["conversation"] = _person_conversation_label(user, user_id)
    info["name"] = _display_user(user, user_id)
    info["email"] = _user_email(user)
    info["user"] = user


def _tui_open_selected_conversation(state, token, self_user_id, cache_path=None):
    if not _tui_selected_conversation(state):
        state["status"] = "no conversations"
        return
    state["mode"] = "conversation"
    state["composer"] = ""
    state["composer_cursor"] = 0
    state["input_active"] = False
    state["stick_bottom"] = False
    state["cursor_row"] = TUI_LATEST_MESSAGE_CURSOR
    state["modal"] = None
    state["status"] = "loading..."
    _tui_hydrate_selected_conversation_label(_tui_selected_conversation(state), token)
    if cache_path:
        _tui_refresh_messages(state, token, self_user_id, force=True, cache_path=cache_path)
    else:
        _tui_refresh_messages(state, token, self_user_id, force=True)
    state["input_active"] = False
    state["stick_bottom"] = False
    _tui_focus_latest_message(state)
    selected = _tui_selected_conversation(state)
    marked_read = _tui_mark_selected_conversation_read(state, token)
    if marked_read and selected:
        _event_cache_mark_read(
            cache_path,
            (selected.get("info") or {}).get("channel_id"),
            _tui_latest_message_ts(state.get("messages") or []),
        )
    message_count = len(state.get("messages") or [])
    mark_error = (selected or {}).get("mark_read_error")
    if mark_error:
        state["status"] = f"{message_count} messages  mark_read:{mark_error}"
    else:
        state["status"] = f"{message_count} messages" + ("  read" if marked_read else "")
    if selected:
        selected["history_loaded"] = True


def _tui_close_conversation(state):
    state["mode"] = "conversations"
    state["composer"] = ""
    state["composer_cursor"] = 0
    state["input_active"] = False
    state["stick_bottom"] = False
    state["modal"] = None
    state["status"] = ""


def _tui_send_composer_message(state, token, self_user_id, cache_path=None):
    text = (state.get("composer") or "").strip()
    if not text:
        return False
    selected = _tui_selected_conversation(state)
    channel_id = ((selected or {}).get("info") or {}).get("channel_id")
    if not channel_id:
        state["status"] = "no selected conversation"
        return False
    state["status"] = "sending..."
    ts = send_post(token, channel_id, text)
    if cache_path and ts:
        info = (selected or {}).get("info") or {"channel_id": channel_id, "surface": "dm", "conversation": channel_id}
        entry = _tui_entry_from_message(
            {"ts": ts, "user": self_user_id, "text": text},
            info,
            {"id": self_user_id, "name": "me", "email": "-", "label": "me"},
            self_user_id,
        )
        _event_cache_store_entries(cache_path, [entry])
    state["composer"] = ""
    state["composer_cursor"] = 0
    if cache_path:
        _tui_refresh_messages(state, token, self_user_id, force=True, cache_path=cache_path)
    else:
        _tui_refresh_messages(state, token, self_user_id, force=True)
    state["status"] = "sent"
    return True


def _tui_delete_word(value):
    value = value.rstrip()
    if not value:
        return ""
    return value[: value.rfind(" ") + 1] if " " in value else ""


def _tui_decode_insert_key(stdscr, curses_module, state, key):
    if key != 27 or state.get("mode") != "conversation" or not state.get("input_active"):
        return key
    try:
        stdscr.timeout(TUI_INSERT_ESCAPE_SEQUENCE_TIMEOUT_MS)
        next_key = stdscr.getch()
    except curses_module.error:
        next_key = -1
    finally:
        try:
            stdscr.timeout(-1)
        except curses_module.error:
            pass
    if next_key in (ord("b"), ord("B")):
        return TUI_ALT_B
    if next_key in (ord("f"), ord("F")):
        return TUI_ALT_F
    return key


def _tui_clamp_composer_cursor(state):
    composer = state.get("composer") or ""
    cursor = int(state.get("composer_cursor") or 0)
    cursor = max(0, min(cursor, len(composer)))
    state["composer_cursor"] = cursor
    return cursor


def _tui_move_cursor_backward_word(value, cursor):
    cursor = max(0, min(int(cursor or 0), len(value)))
    while cursor > 0 and value[cursor - 1].isspace():
        cursor -= 1
    while cursor > 0 and not value[cursor - 1].isspace():
        cursor -= 1
    return cursor


def _tui_move_cursor_forward_word(value, cursor):
    cursor = max(0, min(int(cursor or 0), len(value)))
    while cursor < len(value) and value[cursor].isspace():
        cursor += 1
    while cursor < len(value) and not value[cursor].isspace():
        cursor += 1
    return cursor


def _tui_insert_composer_text(state, value):
    composer = state.get("composer") or ""
    cursor = _tui_clamp_composer_cursor(state)
    state["composer"] = composer[:cursor] + value + composer[cursor:]
    state["composer_cursor"] = cursor + len(value)


def _tui_delete_composer_backward(state):
    composer = state.get("composer") or ""
    cursor = _tui_clamp_composer_cursor(state)
    if cursor <= 0:
        return
    state["composer"] = composer[: cursor - 1] + composer[cursor:]
    state["composer_cursor"] = cursor - 1


def _tui_delete_composer_forward(state):
    composer = state.get("composer") or ""
    cursor = _tui_clamp_composer_cursor(state)
    if cursor >= len(composer):
        return
    state["composer"] = composer[:cursor] + composer[cursor + 1 :]


def _tui_delete_composer_previous_word(state):
    composer = state.get("composer") or ""
    cursor = _tui_clamp_composer_cursor(state)
    new_cursor = _tui_move_cursor_backward_word(composer, cursor)
    state["composer"] = composer[:new_cursor] + composer[cursor:]
    state["composer_cursor"] = new_cursor


def _tui_composer_prompt_view(composer, cursor, width):
    prompt = "> "
    width = max(1, int(width or 1))
    if width <= len(prompt):
        return prompt[:width], min(width - 1, len(prompt))
    field_width = max(1, width - len(prompt))
    cursor = max(0, min(int(cursor or 0), len(composer)))
    max_start = max(len(composer) - field_width, 0)
    if len(composer) <= field_width:
        start = 0
    elif cursor >= field_width:
        start = min(cursor - field_width + 1, max_start)
    else:
        start = 0
    visible = composer[start : start + field_width]
    text = prompt + visible
    cursor_col = len(prompt) + cursor - start
    return _clip(text, width), min(max(cursor_col, 0), width - 1)


def _tui_apply_composer_edit_key(curses_module, state, key):
    if key in (curses_module.KEY_BACKSPACE, CTRL_H, 127):
        _tui_delete_composer_backward(state)
        return True
    if key == CTRL_D:
        _tui_delete_composer_forward(state)
        return True
    if key == CTRL_U:
        state["composer"] = ""
        state["composer_cursor"] = 0
        return True
    if key == CTRL_K:
        cursor = _tui_clamp_composer_cursor(state)
        state["composer"] = (state.get("composer") or "")[:cursor]
        return True
    if key == CTRL_W:
        _tui_delete_composer_previous_word(state)
        return True
    if key in (CTRL_A, curses_module.KEY_HOME):
        state["composer_cursor"] = 0
        return True
    if key in (CTRL_E, curses_module.KEY_END):
        state["composer_cursor"] = len(state.get("composer") or "")
        return True
    if key in (CTRL_B, curses_module.KEY_LEFT):
        state["composer_cursor"] = max(0, _tui_clamp_composer_cursor(state) - 1)
        return True
    if key in (CTRL_F, curses_module.KEY_RIGHT):
        state["composer_cursor"] = min(
            len(state.get("composer") or ""),
            _tui_clamp_composer_cursor(state) + 1,
        )
        return True
    if key == TUI_ALT_B:
        state["composer_cursor"] = _tui_move_cursor_backward_word(
            state.get("composer") or "",
            state.get("composer_cursor") or 0,
        )
        return True
    if key == TUI_ALT_F:
        state["composer_cursor"] = _tui_move_cursor_forward_word(
            state.get("composer") or "",
            state.get("composer_cursor") or 0,
        )
        return True
    if 32 <= key <= 126:
        _tui_insert_composer_text(state, chr(key))
        return True
    return False


def _tui_open_modal_asset_in_editor(stdscr, curses_module, state, token):
    modal = state.get("modal") or {}
    entry = modal.get("entry")
    asset = _tui_selected_modal_asset(state)
    path = _tui_download_asset_open_path(entry, asset, token)
    if not path:
        state["status"] = "no selected file"
        return
    curses_module.def_prog_mode()
    curses_module.endwin()
    try:
        _open_path(path)
    finally:
        curses_module.reset_prog_mode()
        stdscr.keypad(True)
        try:
            curses_module.curs_set(0)
        except curses_module.error:
            pass
    state["status"] = f"opened {path}"


def _erza_conversation_date(row):
    latest = (row or {}).get("latest") or {}
    message = latest.get("message") if isinstance(latest, dict) else {}
    ts = (message or {}).get("ts") or (row or {}).get("sort_ts")
    return format_ts(ts)


def _erza_conversation_from_row(row, chat_api):
    info = (row or {}).get("info") or {}
    channel_id = info.get("channel_id") or info.get("id") or "-"
    return chat_api["ChatConversation"](
        conversation_id=channel_id,
        label=_tui_conversation_label(row),
        date=_erza_conversation_date(row),
        kind=info.get("surface") or "dm",
        unread=bool((row or {}).get("unread_ts")),
        metadata={"row": row},
    )


def _erza_file_from_asset(asset, chat_api):
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    return chat_api["ChatFile"](
        name=asset.get("name") or _asset_filename(asset),
        file_id=str(payload.get("id") or asset.get("file_id") or ""),
        kind=asset.get("kind") or "file",
        metadata={"asset": asset},
    )


def _erza_embed_from_asset(asset, chat_api):
    return chat_api["ChatEmbed"](
        title=asset.get("name") or "Embed",
        url=asset.get("url") or "",
        text=asset.get("text") or "",
        metadata={"asset": asset},
    )


def _erza_message_from_entry(entry, chat_api):
    message = entry.get("message") or {}
    channel_id = entry.get("channel_id") or entry.get("dm_id")
    assets = message_assets(message)
    return chat_api["ChatMessage"](
        message_id=message_id(channel_id, message.get("ts")),
        sender=_tui_sender_name(entry.get("sender") or {}),
        date=format_ts(message.get("ts")),
        text=message.get("text") or "",
        files=[
            _erza_file_from_asset(asset, chat_api)
            for asset in assets
            if asset.get("kind") == "file"
        ],
        embeds=[
            _erza_embed_from_asset(asset, chat_api)
            for asset in assets
            if asset.get("kind") == "embed"
        ],
        unread=bool(entry.get("unread")),
        metadata={"entry": entry},
    )


def _erza_row_for_conversation(conversation):
    metadata = getattr(conversation, "metadata", {}) or {}
    row = metadata.get("row")
    return row if isinstance(row, dict) else None


def _erza_channel_id(conversation):
    row = _erza_row_for_conversation(conversation)
    info = (row or {}).get("info") or {}
    return info.get("channel_id") or getattr(conversation, "conversation_id", None)


def _erza_message_entries(messages):
    entries = []
    for message in messages or []:
        metadata = getattr(message, "metadata", {}) or {}
        entry = metadata.get("entry")
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _build_erza_chat_callbacks(token, self_user_id, chat_api, cache_path=None):
    row_cache = {}

    def load_conversations():
        rows = _tui_load_conversations(token, self_user_id, TUI_RECENT_MESSAGE_LIMIT, cache_path=cache_path)
        row_cache.clear()
        conversations = []
        for row in rows:
            conversation = _erza_conversation_from_row(row, chat_api)
            row_cache[conversation.conversation_id] = row
            conversations.append(conversation)
        return conversations

    def load_messages(conversation):
        row = _erza_row_for_conversation(conversation) or row_cache.get(conversation.conversation_id)
        if not row:
            return []
        _tui_hydrate_selected_conversation_label(row, token)
        entries = _tui_load_messages(row, token, self_user_id, force=True, cache_path=cache_path)
        conversation.label = _tui_conversation_label(row)
        conversation.date = _erza_conversation_date(row)
        conversation.unread = bool(row.get("unread_ts"))
        return [_erza_message_from_entry(entry, chat_api) for entry in entries]

    def send_message(conversation, text):
        channel_id = _erza_channel_id(conversation)
        if not channel_id:
            raise SystemExit("no selected conversation")
        ts = send_post(token, channel_id, text)
        if cache_path and ts:
            row = _erza_row_for_conversation(conversation) or row_cache.get(conversation.conversation_id) or {}
            info = row.get("info") or {"channel_id": channel_id, "surface": "dm", "conversation": channel_id}
            entry = _tui_entry_from_message(
                {"ts": ts, "user": self_user_id, "text": text},
                info,
                {"id": self_user_id, "name": "me", "email": "-", "label": "me"},
                self_user_id,
            )
            _event_cache_store_entries(cache_path, [entry])
        return ts

    def mark_read(conversation, messages):
        row = _erza_row_for_conversation(conversation) or row_cache.get(conversation.conversation_id)
        entries = _erza_message_entries(messages)
        if not row:
            return False
        row["messages"] = entries
        marked, error = _tui_mark_conversation_row_read(row, token, cache_path=cache_path)
        if not marked:
            return error
        conversation.unread = False
        return True

    def mark_all_read(conversations):
        marked = 0
        failed = []
        for conversation in conversations or []:
            row = _erza_row_for_conversation(conversation) or row_cache.get(conversation.conversation_id)
            if not row:
                continue
            if not bool(row.get("unread_ts")) and not bool(getattr(conversation, "unread", False)):
                continue
            marked_read, error = _tui_mark_conversation_row_read(row, token, cache_path=cache_path)
            if marked_read:
                conversation.unread = False
                marked += 1
            else:
                failed.append(error)
        if failed:
            return f"{marked} marked, {len(failed)} failed: {failed[0]}"
        return marked

    def open_file(conversation, message, file_item):
        del conversation
        metadata = getattr(file_item, "metadata", {}) or {}
        asset = metadata.get("asset")
        entry_metadata = getattr(message, "metadata", {}) or {}
        entry = entry_metadata.get("entry")
        if not isinstance(entry, dict) or not isinstance(asset, dict):
            return None
        return _tui_download_asset_open_path(entry, asset, token)

    return chat_api["ChatCallbacks"](
        load_conversations=load_conversations,
        load_messages=load_messages,
        send_message=send_message,
        mark_read=mark_read,
        open_file=open_file,
        mark_all_read=mark_all_read,
    )


def _run_erza_chat_tui(token, self_user_id, cache_path=None):
    chat_api = _load_erza_chat_api()
    if chat_api is None:
        return False
    callbacks = _build_erza_chat_callbacks(token, self_user_id, chat_api, cache_path=cache_path)
    chat_api["run_chat_app"](callbacks, title="slack tui")
    return True


def _setup_tui_curses(stdscr, curses_module):
    try:
        curses_module.curs_set(0)
    except curses_module.error:
        pass
    stdscr.keypad(True)
    try:
        curses_module.noecho()
        curses_module.raw()
        curses_module.nonl()
    except curses_module.error:
        pass
    try:
        curses_module.start_color()
        curses_module.use_default_colors()
        assume_default = getattr(curses_module, "assume_default_colors", None)
        if assume_default:
            assume_default(-1, -1)
        curses_module.init_pair(1, -1, -1)
        stdscr.bkgd(" ", curses_module.color_pair(1))
    except curses_module.error:
        pass


def _run_tui(stdscr, token, self_user_id, cache_path=None):
    global curses
    import curses

    _setup_tui_curses(stdscr, curses)
    state = {
        "mode": "conversations",
        "conversations": [],
        "conversation_index": 0,
        "conversation_scroll": 0,
        "messages": [],
        "message_index": 0,
        "message_scroll": 0,
        "message_view_height": 1,
        "rendered_line_count": 0,
        "rendered_rows": [],
        "cursor_row": 0,
        "stick_bottom": False,
        "input_active": False,
        "modal": None,
        "show_help": False,
        "composer": "",
        "composer_cursor": 0,
        "leader_buffer": "",
        "status": "loading...",
    }
    _tui_draw(stdscr, state)
    _tui_refresh(state, token, self_user_id, cache_path=cache_path)
    while True:
        _tui_draw(stdscr, state)
        key = stdscr.getch()
        key = _tui_decode_insert_key(stdscr, curses, state, key)
        if state.get("show_help"):
            if key in (ord("?"), 27, ord("h")):
                state["show_help"] = False
                continue
            if key in (ord("q"),):
                return
            continue
        if key in (ord("?"),):
            state["show_help"] = True
            continue
        if state.get("modal"):
            if key in (27, ord("h")):
                state["modal"] = None
                continue
            if key in (ord("q"),):
                return
            if key in (ord("j"), curses.KEY_DOWN):
                _tui_move_file_modal(state, 1)
                continue
            if key in (ord("k"), curses.KEY_UP):
                _tui_move_file_modal(state, -1)
                continue
            if key in (ord("l"), curses.KEY_ENTER, 10, 13):
                _tui_open_modal_asset_in_editor(stdscr, curses, state, token)
                continue
            continue
        if state.get("mode") == "conversations":
            if key in (ord("q"), 27):
                return
            if key in (ord("r"),):
                state["status"] = "loading..."
                _tui_draw(stdscr, state)
                _tui_refresh(state, token, self_user_id, cache_path=cache_path)
                continue
            if key in (ord("g"),):
                state["conversation_index"] = 0
                continue
            if key in (ord("G"),):
                state["conversation_index"] = max(0, len(state.get("conversations") or []) - 1)
                continue
            if key in (ord("j"), curses.KEY_DOWN):
                max_index = max(0, len(state.get("conversations") or []) - 1)
                state["conversation_index"] = min(max_index, int(state.get("conversation_index") or 0) + 1)
                continue
            if key in (ord("k"), curses.KEY_UP):
                state["conversation_index"] = max(0, int(state.get("conversation_index") or 0) - 1)
                continue
            if key in (ord("l"), ord("\n"), curses.KEY_ENTER, 10, 13):
                state["mode"] = "conversation"
                state["status"] = "loading..."
                _tui_draw(stdscr, state)
                _tui_open_selected_conversation(state, token, self_user_id, cache_path=cache_path)
                continue
            continue

        input_active = bool(state.get("input_active", False))
        if input_active:
            if key in (27,):
                state["input_active"] = False
                state["stick_bottom"] = False
                _tui_focus_latest_message(state)
                continue
            if key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                composer = state.get("composer") or ""
                if composer.strip():
                    state["status"] = "sending..."
                    _tui_draw(stdscr, state)
                    try:
                        _tui_send_composer_message(state, token, self_user_id, cache_path=cache_path)
                    except SystemExit as exc:
                        state["status"] = str(exc)
                continue
            if _tui_apply_composer_edit_key(curses, state, key):
                continue
            continue

        if key in (ord("q"),):
            return
        if _tui_apply_leader_key(state, key, token, cache_path=cache_path):
            continue
        if key in (ord("h"),):
            _tui_close_conversation(state)
            continue
        if key in (ord("i"),):
            state["input_active"] = True
            state["stick_bottom"] = True
            continue
        if key in (ord("r"),):
            state["status"] = "loading..."
            _tui_draw(stdscr, state)
            _tui_refresh_messages(state, token, self_user_id, force=True, cache_path=cache_path)
            state["input_active"] = False
            state["stick_bottom"] = False
            _tui_focus_latest_message(state)
            state["status"] = f"{len(state.get('messages') or [])} messages"
            continue
        if key in (ord("j"), curses.KEY_DOWN):
            _tui_move_cursor_row(state, 1)
            continue
        if key in (ord("k"), curses.KEY_UP):
            _tui_move_cursor_row(state, -1)
            continue
        if key in (CTRL_N,):
            _tui_move_message_row(state, 1)
            continue
        if key in (CTRL_P,):
            _tui_move_message_row(state, -1)
            continue
        if key in (ord("G"), curses.KEY_END):
            _tui_focus_latest_message(state)
            state["stick_bottom"] = False
            continue
        if key in (ord("g"),):
            rows = state.get("rendered_rows") or []
            first_message_index = _tui_first_message_row_index(rows)
            state["cursor_row"] = first_message_index if first_message_index is not None else 0
            state["stick_bottom"] = False
            continue
        if key in (ord("l"),):
            _tui_open_file_modal_for_row(state, _tui_selected_file_row(state))
            continue


def run_slack_tui(token, self_user_id, cache_path=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("slack tui requires an interactive terminal.")
    if _run_erza_chat_tui(token, self_user_id, cache_path=cache_path):
        return
    import curses

    curses.wrapper(_run_tui, token, self_user_id, cache_path)


def open_dm_messages(dm_id, token, self_user_id):
    parsed_message_id = parse_message_id(dm_id)
    if parsed_message_id:
        channel_id, target_ts = parsed_message_id
        try:
            contact_dm = get_dm_info(channel_id, token)
        except SystemExit:
            contact_dm = _fallback_conversation_summary(channel_id)
        history = slack_request(
            "conversations.history",
            {
                "channel": channel_id,
                "latest": target_ts,
                "inclusive": "true",
                "limit": "1",
            },
            token,
            http_method="GET",
        )
        messages = history.get("messages") or []
        message = next(
            (item for item in messages if str(item.get("ts") or "") == target_ts),
            messages[0] if messages else None,
        )
        if not message:
            print("No DM messages.")
            return
        user_cache = {}
        entry = {
            "sort_ts": float(target_ts),
            "email": contact_dm["email"],
            "dm_id": channel_id,
            "channel_id": channel_id,
            "surface": contact_dm.get("surface") or "conversation",
            "conversation": contact_dm.get("conversation")
            or contact_dm.get("name")
            or contact_dm["email"],
            "members": contact_dm.get("members") or "-",
            "message": message,
            "sender": _sender_info(message, token, user_cache),
        }
        _print_open_entries([entry], token)
        slack_request(
            "conversations.mark",
            {"channel": channel_id, "ts": target_ts},
            token,
            use_form=True,
        )
        print(f"opened_and_marked_read message_id={message_id(channel_id, target_ts)}")
        return

    try:
        contact_dm = get_dm_info(dm_id, token)
    except SystemExit:
        contact_dm = _fallback_conversation_summary(dm_id)
    info = contact_dm["info"]
    last_read = info.get("last_read") or "0"
    try:
        last_read_value = float(last_read)
    except (TypeError, ValueError):
        last_read_value = 0.0

    history = slack_request(
        "conversations.history",
        {"channel": dm_id, "limit": "200"},
        token,
        http_method="GET",
    )
    external = []
    user_cache = {}
    for message in history.get("messages") or []:
        ts = message.get("ts")
        if not ts or message.get("user") == self_user_id:
            continue
        try:
            ts_value = float(ts)
        except (TypeError, ValueError):
            continue
        external.append(
            {
                "sort_ts": ts_value,
                "email": contact_dm["email"],
                "dm_id": dm_id,
                "channel_id": dm_id,
                "surface": contact_dm.get("surface") or "conversation",
                "conversation": contact_dm.get("conversation")
                or contact_dm.get("name")
                or contact_dm["email"],
                "members": contact_dm.get("members") or "-",
                "message": message,
                "sender": _sender_info(message, token, user_cache),
                "unread": ts_value > last_read_value,
            }
        )

    if not external:
        print("No DM messages.")
        return

    unread = [item for item in external if item["unread"]]
    selected = unread if unread else [max(external, key=lambda item: item["sort_ts"])]
    selected.sort(key=lambda item: item["sort_ts"])
    _print_open_entries(selected, token)

    latest_ts = selected[-1]["message"].get("ts")
    if latest_ts:
        slack_request(
            "conversations.mark",
            {"channel": dm_id, "ts": latest_ts},
            token,
            use_form=True,
        )
        print(f"opened_and_marked_read channel_id={dm_id} ts={latest_ts}")


def action_close_conversation(channel_id, token):
    data = slack_request("conversations.close", {"channel": channel_id}, token)
    return data.get("already_closed") or data.get("no_op")


def action_leave_conversation(channel_id, token):
    data = slack_request("conversations.leave", {"channel": channel_id}, token)
    return data.get("already_inactive") or data.get("not_in_channel")


def conversation_age_is_stale(ts_value, cutoff_ts):
    if not ts_value:
        return False
    try:
        return float(ts_value) < cutoff_ts
    except (TypeError, ValueError):
        return False


def ms_age_is_stale(ms_value, cutoff_ts):
    if not ms_value:
        return False
    try:
        return (float(ms_value) / 1000.0) < cutoff_ts
    except (TypeError, ValueError):
        return False


def summarize_reasons(reasons):
    return ",".join(reasons) if reasons else "-"


def mark_all_unread_dms_as_read(contacts, token):
    rows = []
    marked = 0

    for contact_dm in get_contact_dm_infos(contacts, token):
        info = contact_dm["info"]
        unread = info.get("unread_count_display") or info.get("unread_count") or 0
        if unread <= 0:
            continue

        latest = info.get("latest") or {}
        latest_ts = latest.get("ts") if isinstance(latest, dict) else None
        if not latest_ts:
            continue

        user = contact_dm["user"]
        profile = user.get("profile") or {}
        display_name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("name")
            or contact_dm["user_id"]
        )

        slack_request(
            "conversations.mark",
            {"channel": contact_dm["channel_id"], "ts": latest_ts},
            token,
            use_form=True,
        )
        marked += 1
        rows.append(
            [
                ("label", contact_dm["label"]),
                ("name", display_name),
                ("email", contact_dm["email"]),
                ("dm_id", contact_dm["channel_id"]),
                ("unread", str(unread)),
                ("date", format_ts(latest_ts)),
                ("action", "marked_read"),
            ]
        )

    if not rows:
        print("No unread DMs to mark as read.")
        return

    print_sections(rows)
    print(f"Summary: marked_read={marked}")


def download_dm_file(dm_id, file_id, output_path, token):
    cursor = None
    while True:
        history = slack_request(
            "conversations.history",
            {
                "channel": dm_id,
                "limit": "200",
                **({"cursor": cursor} if cursor else {}),
            },
            token,
            http_method="GET",
        )
        for message in history.get("messages") or []:
            for file in message_files(message):
                if file.get("id") != file_id:
                    continue
                download_url = file.get("url_private_download")
                filename = file.get("name") or file_id
                if not download_url:
                    raise SystemExit("File has no downloadable URL.")

                destination = output_path or filename
                destination = os.path.expanduser(destination)
                _download_file_to_path(download_url, destination, token)

                print(f"downloaded channel_id={dm_id} file_id={file_id} path={destination}")
                return

        cursor = (
            (history.get("response_metadata") or {}).get("next_cursor") or ""
        ).strip()
        if not cursor:
            break

    raise SystemExit(f"File not found in channel_id={dm_id}: {file_id}")


def clear_stale_conversations(token):
    cutoff_ts = time.time() - (183 * 24 * 60 * 60)
    user_cache = {}
    rows = []
    counts = {"closed": 0, "left": 0, "skipped": 0}

    dm_channels = list_api(
        "users.conversations",
        {"types": "im", "exclude_archived": "true", "limit": "200"},
        token,
    )
    for channel in dm_channels:
        channel_id = channel.get("id")
        if not channel_id:
            continue
        info = slack_request(
            "conversations.info",
            {"channel": channel_id, "include_num_members": "false"},
            token,
            http_method="GET",
        ).get("channel") or {}
        user_id = info.get("user") or channel.get("user") or "-"
        if user_id not in user_cache:
            user_cache[user_id] = get_user_info(user_id, token)
        user = user_cache[user_id]
        profile = user.get("profile") or {}
        email = profile.get("email") or "-"
        display_name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("name")
            or user_id
        )
        latest = info.get("latest") or {}
        latest_ts = latest.get("ts") if isinstance(latest, dict) else None
        reasons = []
        if email == "-":
            reasons.append("no_email")
        if conversation_age_is_stale(latest_ts, cutoff_ts):
            reasons.append("stale_6mo")
        if not reasons:
            continue
        try:
            action_close_conversation(channel_id, token)
            counts["closed"] += 1
            action = "closed"
        except SystemExit as exc:
            counts["skipped"] += 1
            action = f"skip:{exc}"
        rows.append(
            [
                ("type", "dm"),
                ("action", action),
                ("why", summarize_reasons(reasons)),
                ("name", display_name),
                ("email", email),
                ("id", channel_id),
            ]
        )

    public_channels = list_api(
        "conversations.list",
        {"types": "public_channel", "exclude_archived": "true", "limit": "200"},
        token,
    )
    for channel in public_channels:
        if not channel.get("is_member"):
            continue
        channel_id = channel.get("id")
        if not channel_id:
            continue
        info = slack_request(
            "conversations.info",
            {"channel": channel_id, "include_num_members": "false"},
            token,
            http_method="GET",
        ).get("channel") or {}
        creator_id = info.get("creator") or channel.get("creator") or "-"
        if creator_id not in user_cache:
            user_cache[creator_id] = get_user_info(creator_id, token)
        creator = user_cache[creator_id]
        creator_email = ((creator.get("profile") or {}).get("email")) or "-"
        reasons = []
        if creator_email == "-":
            reasons.append("creator_no_email")
        if ms_age_is_stale(info.get("updated") or channel.get("updated"), cutoff_ts):
            reasons.append("stale_6mo")
        if not reasons:
            continue
        if info.get("is_general"):
            counts["skipped"] += 1
            rows.append(
                [
                    ("type", "chan"),
                    ("action", "skip:cant_leave_general"),
                    ("why", summarize_reasons(reasons)),
                    ("name", info.get("name") or channel.get("name") or channel_id),
                    ("email", creator_email),
                    ("id", channel_id),
                ]
            )
            continue
        try:
            action_leave_conversation(channel_id, token)
            counts["left"] += 1
            action = "left"
        except SystemExit as exc:
            counts["skipped"] += 1
            action = f"skip:{exc}"
        rows.append(
            [
                ("type", "chan"),
                ("action", action),
                ("why", summarize_reasons(reasons)),
                ("name", info.get("name") or channel.get("name") or channel_id),
                ("email", creator_email),
                ("id", channel_id),
            ]
        )

    if not rows:
        print("No conversations cleared.")
    else:
        print_sections(rows)
    print(
        f"Summary: closed={counts['closed']} left={counts['left']} skipped={counts['skipped']} private_and_mpim_skipped=scope"
    )


def _state_base_dir():
    base = os.getenv("XDG_STATE_HOME")
    if base:
        return Path(os.path.expandvars(base)).expanduser() / "slack"
    return Path.home() / ".local" / "state" / "slack"


def _safe_preset_slug(preset):
    return re.sub(r"[^A-Za-z0-9_.@-]+", "_", str(preset or "default"))


def _expand_path(value):
    return Path(os.path.expandvars(str(value))).expanduser()


def _account_string(account, key, default="", required=False):
    value = account.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise SystemExit(f"config key must be a string: {key}")
    value = value.strip()
    if required and not value:
        raise SystemExit(f"missing required config key: {key}")
    return value


def _account_int(account, key, default):
    value = account.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemExit(f"config key must be an integer: {key}")
    return value


def _account_string_list(account, key, default=None):
    value = account.get(key, default if default is not None else [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"config key must be a list of strings: {key}")
    return [item for item in value if item]


def _codex_state_paths(account, preset):
    slug = _safe_preset_slug(preset)
    base = _state_base_dir()
    return {
        "state_file": _expand_path(account.get("codex_state_file") or str(base / f"codex-{slug}.json")),
        "log_file": _expand_path(account.get("codex_log_file") or str(base / f"codex-{slug}.log")),
        "lock_file": _expand_path(account.get("codex_lock_file") or str(base / f"codex-{slug}.lock")),
    }


def _read_state(state_file):
    if not state_file.exists():
        return {}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(state_file, payload):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _state_recent_keys(state):
    keys = state.get("recent_event_keys")
    if not isinstance(keys, list):
        return []
    return [str(item) for item in keys[-100:]]


def _event_key(event):
    return ":".join(
        str(item or "")
        for item in (
            event.get("type"),
            event.get("channel"),
            event.get("user"),
            event.get("thread_ts") or event.get("ts") or event.get("event_ts"),
        )
    )


def _claim_event(account, preset, event):
    paths = _codex_state_paths(account, preset)
    state = _read_state(paths["state_file"])
    key = _event_key(event)
    recent = _state_recent_keys(state)
    if key and key in recent:
        return False
    if key:
        recent.append(key)
        state["recent_event_keys"] = recent[-100:]
    state["last_event_key"] = key
    state["last_event_at"] = datetime.now().astimezone().isoformat()
    _write_state(paths["state_file"], state)
    return True


def _mark_codex_processed(account, preset, event, reply_ts=None):
    paths = _codex_state_paths(account, preset)
    state = _read_state(paths["state_file"])
    state["processed"] = int(state.get("processed") or 0) + 1
    state["last_processed_at"] = datetime.now().astimezone().isoformat()
    state["last_channel"] = event.get("channel") or ""
    state["last_message_ts"] = event.get("ts") or event.get("event_ts") or ""
    if reply_ts:
        state["last_reply_ts"] = reply_ts
    state["last_error"] = ""
    _write_state(paths["state_file"], state)


def _mark_codex_error(account, preset, message):
    paths = _codex_state_paths(account, preset)
    state = _read_state(paths["state_file"])
    state["last_error"] = str(message)
    state["last_error_at"] = datetime.now().astimezone().isoformat()
    _write_state(paths["state_file"], state)


def _codex_log(account, preset, message):
    paths = _codex_state_paths(account, preset)
    paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
    with paths["log_file"].open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().astimezone().isoformat()} {message}\n")


@contextmanager
def _codex_lock(account, preset):
    paths = _codex_state_paths(account, preset)
    paths["lock_file"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock_file"].open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def _websocket_module():
    try:
        import websocket
    except ImportError as exc:
        raise SystemExit("Missing dependency: websocket-client. Run: pip install -r requirements.txt") from exc
    return websocket


def _open_socket_mode_connection(app_token):
    data = slack_request("apps.connections.open", {}, app_token, use_form=True)
    url = data.get("url")
    if not url:
        raise SystemExit("Slack did not return a Socket Mode WebSocket URL.")
    websocket = _websocket_module()
    return websocket.create_connection(url, timeout=70)


def _ack_socket_envelope(socket, envelope):
    envelope_id = envelope.get("envelope_id")
    if not envelope_id:
        return
    socket.send(json.dumps({"envelope_id": envelope_id}))


def _strip_bot_mention(text, bot_user_id):
    if not bot_user_id:
        return text.strip()
    return re.sub(rf"<@{re.escape(bot_user_id)}>\s*", "", text or "").strip()


def _eligible_slack_event(event, bot_user_id):
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    user_id = event.get("user")
    if not user_id or user_id == bot_user_id:
        return None
    if event.get("bot_id") or event.get("bot_profile"):
        return None

    channel_id = event.get("channel") or ""
    text = (event.get("text") or "").strip()
    if event_type == "app_mention":
        return {
            "kind": "app_mention",
            "channel_id": channel_id,
            "user_id": user_id,
            "text": _strip_bot_mention(text, bot_user_id),
            "thread_ts": event.get("thread_ts") or event.get("ts"),
            "ts": event.get("ts") or event.get("event_ts"),
            "raw": event,
        }
    if event_type == "message":
        if event.get("subtype"):
            return None
        channel_type = event.get("channel_type") or ""
        if channel_type != "im" and not str(channel_id).startswith("D"):
            return None
        return {
            "kind": "direct_message",
            "channel_id": channel_id,
            "user_id": user_id,
            "text": text,
            "thread_ts": event.get("thread_ts"),
            "ts": event.get("ts") or event.get("event_ts"),
            "raw": event,
        }
    return None


def _event_cache_eligible_message(event):
    if not isinstance(event, dict) or event.get("type") != "message":
        return None
    if event.get("subtype"):
        return None
    channel_id = event.get("channel") or ""
    channel_type = event.get("channel_type") or ""
    if channel_type not in {"im", "mpim"} and not str(channel_id).startswith(("D", "G")):
        return None
    return event


def _event_cache_entry_from_event(event, token, self_user_id, user_cache, conversation_cache):
    event = _event_cache_eligible_message(event)
    if not event:
        return None
    channel_id = event.get("channel")
    if not channel_id:
        return None
    if channel_id not in conversation_cache:
        channel_hint = {
            "id": channel_id,
            "is_im": event.get("channel_type") == "im" or str(channel_id).startswith("D"),
            "is_mpim": event.get("channel_type") == "mpim",
        }
        try:
            conversation_cache[channel_id] = _conversation_summary(channel_hint, token, user_cache)
        except SystemExit:
            conversation_cache[channel_id] = _fallback_conversation_summary(channel_id, channel_hint)
    info = conversation_cache[channel_id]
    if info.get("surface") not in {"dm", "group_dm"}:
        return None
    try:
        sender = _sender_info(event, token, user_cache)
    except SystemExit:
        user_id = event.get("user") or "-"
        sender = {"id": user_id, "name": user_id, "email": "-", "label": user_id}
    return _tui_entry_from_message(event, info, sender, self_user_id)


def _event_cache_store_socket_payload(account, preset, payload, token, self_user_id, user_cache, conversation_cache):
    event = payload.get("event") if isinstance(payload, dict) else {}
    entry = _event_cache_entry_from_event(event, token, self_user_id, user_cache, conversation_cache)
    if not entry:
        return False
    cache_path = _event_cache_db_path(account, preset)
    with _event_cache_connect(cache_path) as conn:
        stored = _event_cache_upsert_entry(
            conn,
            entry,
            event_id=str(payload.get("event_id") or ""),
            history_loaded=False,
        )
        if stored:
            _event_cache_set_state(conn, "last_event_at", _event_cache_now())
            _event_cache_set_state(conn, "last_channel", entry.get("channel_id") or "")
            _event_cache_set_state(conn, "last_message_ts", (entry.get("message") or {}).get("ts") or "")
            count = int(_event_cache_get_state(conn, "processed_events", "0") or 0) + 1
            _event_cache_set_state(conn, "processed_events", str(count))
        conn.commit()
    if stored:
        _events_log(account, preset, f"cached channel={entry.get('channel_id')} ts={(entry.get('message') or {}).get('ts')}")
    return stored


def _event_cache_socket_context(account, preset, log_func=None):
    if account.get("events_cache_from_socket") is False:
        return None
    try:
        token = resolve_list_token(account)
        auth_data = auth_test(token)
    except SystemExit as exc:
        if log_func:
            log_func(account, preset, f"event cache disabled: {exc}")
        return None
    self_user_id = auth_data.get("user_id")
    if not self_user_id:
        if log_func:
            log_func(account, preset, "event cache disabled: missing Slack user id")
        return None
    return {
        "token": token,
        "self_user_id": self_user_id,
        "user_cache": {},
        "conversation_cache": {},
    }


def _event_cache_store_socket_payload_from_context(account, preset, payload, context, log_func=None):
    if not context:
        return False
    try:
        return _event_cache_store_socket_payload(
            account,
            preset,
            payload,
            context["token"],
            context["self_user_id"],
            context["user_cache"],
            context["conversation_cache"],
        )
    except SystemExit as exc:
        if log_func:
            log_func(account, preset, f"event cache error: {exc}")
    except Exception as exc:
        if log_func:
            log_func(account, preset, f"event cache error: {exc}")
    return False


def events_sync_once(account, preset, *, quiet=False):
    token = resolve_list_token(account)
    auth_data = auth_test(token)
    self_user_id = auth_data.get("user_id")
    if not self_user_id:
        raise SystemExit("Unable to determine the current Slack user.")
    cache_path = _event_cache_db_path(account, preset)
    rows = _tui_load_conversations(token, self_user_id, TUI_RECENT_MESSAGE_LIMIT, cache_path=None)
    stored_messages = 0
    for row in rows:
        _event_cache_store_conversation_row(cache_path, row, history_loaded=bool(row.get("history_loaded")))
    for row in rows[: _account_int(account, "events_sync_conversation_limit", EVENT_SYNC_CONVERSATION_LIMIT)]:
        _tui_hydrate_selected_conversation_label(row, token)
        entries = _tui_load_messages(row, token, self_user_id, force=True, cache_path=cache_path)
        row["messages"] = entries
        row["history_loaded"] = True
        stored_messages += _event_cache_store_conversation_row(cache_path, row, history_loaded=True)
    with _event_cache_connect(cache_path) as conn:
        _event_cache_set_state(conn, "last_sync_at", _event_cache_now())
        _event_cache_set_state(conn, "last_sync_conversations", str(len(rows)))
        _event_cache_set_state(conn, "last_sync_messages", str(stored_messages))
        conn.commit()
    if not quiet:
        print(f"events_sync conversations={len(rows)} messages={stored_messages} cache={cache_path}")
    return stored_messages


def _events_socket_loop(account, preset, *, once=False):
    app_token = resolve_app_token(account)
    token = resolve_list_token(account)
    auth_data = auth_test(token)
    self_user_id = auth_data.get("user_id")
    if not self_user_id:
        raise SystemExit("Unable to determine the current Slack user.")
    socket = _open_socket_mode_connection(app_token)
    socket.settimeout(_account_int(account, "events_socket_timeout_seconds", EVENT_SOCKET_TIMEOUT_SECONDS))
    user_cache = {}
    conversation_cache = {}
    processed = 0
    try:
        while True:
            try:
                raw = socket.recv()
            except Exception as exc:
                if once:
                    _events_log(account, preset, f"once timeout/no event: {exc}")
                    return processed
                raise
            if not raw:
                continue
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                continue
            envelope_type = envelope.get("type")
            if envelope_type == "hello":
                _events_log(account, preset, "socket connected")
                continue
            if envelope_type == "disconnect":
                _events_log(account, preset, f"socket disconnect: {envelope.get('reason') or '-'}")
                return processed
            if "envelope_id" in envelope:
                _ack_socket_envelope(socket, envelope)
            if envelope_type != "events_api":
                continue
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
            if _event_cache_store_socket_payload(account, preset, payload, token, self_user_id, user_cache, conversation_cache):
                processed += 1
            if once and processed:
                return processed
    finally:
        try:
            socket.close()
        except Exception:
            pass


def events_once(account, preset):
    processed = _events_socket_loop(account, preset, once=True)
    print(f"events_once processed={processed}")
    return 0


def _events_log(account, preset, message):
    paths = _event_cache_paths(account, preset)
    paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
    with paths["log_file"].open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().astimezone().isoformat()} {message}\n")


def _events_sync_loop(account, preset, stop_event):
    interval = max(60, _account_int(account, "events_sync_seconds", EVENT_SYNC_SECONDS))
    while not stop_event.is_set():
        try:
            events_sync_once(account, preset, quiet=True)
            _events_log(account, preset, "sync complete")
        except SystemExit as exc:
            _events_log(account, preset, f"sync error: {exc}")
        except Exception as exc:
            _events_log(account, preset, f"sync error: {exc}")
        stop_event.wait(interval)


def events_service(account, preset):
    account = dict(account)
    account["_preset"] = preset
    _events_log(account, preset, "service started")
    stop_event = threading.Event()
    syncer = threading.Thread(target=_events_sync_loop, args=(account, preset, stop_event), daemon=True)
    syncer.start()
    codex_unit = f"{_codex_unit_name(preset)}.service"
    if _systemd_user_service_active(codex_unit):
        _events_log(account, preset, f"socket skipped: {codex_unit} active; using sync-only cache service")
        while True:
            stop_event.wait(3600)
    while True:
        try:
            _events_socket_loop(account, preset, once=False)
            time.sleep(5)
        except SystemExit as exc:
            _events_log(account, preset, f"service error: {exc}")
            time.sleep(5)
        except Exception as exc:
            _events_log(account, preset, f"service error: {exc}")
            time.sleep(5)


def _events_unit_name(preset):
    return f"slack-events-{_safe_preset_slug(preset)}"


def _events_unit_path(preset):
    return _systemd_unit_dir() / f"{_events_unit_name(preset)}.service"


def write_events_unit(preset):
    unit_path = _events_unit_path(preset)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        "\n".join(
            [
                "[Unit]",
                f"Description=Slack preset {preset} realtime DM/GDM event cache",
                "After=network-online.target",
                "",
                "[Service]",
                "Type=simple",
                "Environment=PYTHONUNBUFFERED=1",
                f"ExecStart=%h/.local/bin/slack {preset} events service",
                "Restart=always",
                "RestartSec=5",
                "Nice=5",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    if shutil.which("systemd-analyze") is not None:
        result = subprocess.run(
            ["systemd-analyze", "--user", "verify", str(unit_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SystemExit(f"systemd unit validation failed: {detail}")
    return unit_path


def events_install_service(preset):
    write_events_unit(preset)
    unit = f"{_events_unit_name(preset)}.service"
    _systemctl_user("daemon-reload")
    _systemctl_user("enable", "--now", unit)
    _systemctl_user("restart", unit)
    print(f"service enabled: {unit}")
    return 0


def events_disable_service(preset):
    write_events_unit(preset)
    unit = f"{_events_unit_name(preset)}.service"
    _systemctl_user("disable", "--now", unit, check=False)
    _systemctl_user("daemon-reload")
    print(f"service disabled: {unit}")
    return 0


def events_service_status(preset):
    result = subprocess.run(
        ["systemctl", "--user", "status", f"{_events_unit_name(preset)}.service", "--no-pager"],
        check=False,
        text=True,
    )
    return result.returncode


def events_service_logs(preset, lines=80):
    result = subprocess.run(
        ["journalctl", "--user", "-u", f"{_events_unit_name(preset)}.service", "-n", str(lines), "--no-pager"],
        check=False,
        text=True,
    )
    return result.returncode


def events_status(account, preset):
    paths = _event_cache_paths(account, preset)
    state = {
        "cache": str(paths["db_file"]),
        "log": str(paths["log_file"]),
        "exists": paths["db_file"].exists(),
        "has_app_token": bool(_has_token(account, "app") or _read_token_file(DEFAULT_APP_TOKEN_FILE)),
        "has_user_token": bool(_has_token(account, "user") or _read_token_file(DEFAULT_USER_TOKEN_FILE)),
    }
    if paths["db_file"].exists():
        with _event_cache_connect(paths["db_file"]) as conn:
            state.update(
                {
                    "conversations": conn.execute("SELECT COUNT(*) AS count FROM conversations").fetchone()["count"],
                    "messages": conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()["count"],
                    "processed_events": _event_cache_get_state(conn, "processed_events", "0"),
                    "last_event_at": _event_cache_get_state(conn, "last_event_at", ""),
                    "last_sync_at": _event_cache_get_state(conn, "last_sync_at", ""),
                    "last_channel": _event_cache_get_state(conn, "last_channel", ""),
                    "last_message_ts": _event_cache_get_state(conn, "last_message_ts", ""),
                }
            )
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def events_reset_cache(account, preset):
    paths = _event_cache_paths(account, preset)
    for path in (paths["db_file"], Path(str(paths["db_file"]) + "-wal"), Path(str(paths["db_file"]) + "-shm")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _events_log(account, preset, "cache reset")
    print(f"cache reset: {paths['db_file']}")
    return 0


def print_events_help():
    print(
        """Usage:
  slack <preset> events sync
  slack <preset> events once
  slack <preset> events service
  slack <preset> events ti
  slack <preset> events td
  slack <preset> events st
  slack <preset> events logs [lines]
  slack <preset> events status
  slack <preset> events reset-cache"""
    )
    return 0


def _event_query(event_info):
    return event_info.get("text") or ""


def _render_codex_prompt_template(template, event_info):
    rendered = str(template)
    replacements = {
        "{}": _event_query(event_info),
        "{query}": _event_query(event_info),
        "{text}": _event_query(event_info),
        "{kind}": event_info.get("kind") or "",
        "{channel_id}": event_info.get("channel_id") or "",
        "{user_id}": event_info.get("user_id") or "",
        "{message_ts}": event_info.get("ts") or "",
        "{thread_ts}": event_info.get("thread_ts") or "",
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, str(value))
    return rendered


def _codex_prompt_for_slack(account, event_info):
    template = account.get("codex_prompt") or account.get("codex_wrapper_prompt")
    if isinstance(template, str) and template.strip():
        return _render_codex_prompt_template(template, event_info)
    thread = event_info.get("thread_ts") or "-"
    return f"""Slack message for Ryan.

You are replying through Ryan's local Slack event service. Produce only the Slack reply text.
If you cannot complete the request safely from the available context, say what is missing.

Slack event:
- kind: {event_info.get("kind")}
- channel_id: {event_info.get("channel_id")}
- user_id: {event_info.get("user_id")}
- message_ts: {event_info.get("ts")}
- thread_ts: {thread}

Message:
{event_info.get("text") or ""}
"""


def _strip_code_fence(text):
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _parse_codex_reply_directive(reply, require_directive=False):
    text = _strip_code_fence(reply)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        normalized = re.sub(r'([{,]\s*)(respond|response)\s*:', r'\1"\2":', text)
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            return (False, "") if require_directive else (True, reply)
    if not isinstance(payload, dict) or "respond" not in payload:
        return (False, "") if require_directive else (True, reply)
    respond = payload.get("respond")
    should_respond = respond is True or respond == 1 or str(respond).strip().lower() in {"1", "true", "yes"}
    response = payload.get("response") or ""
    return should_respond and bool(str(response).strip()), str(response).strip()


def _codex_requires_structured_reply(account):
    for key in ("codex_prompt", "codex_wrapper_prompt"):
        value = account.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def codex_resume_for_slack(account, event_info):
    session_id = _account_string(account, "codex_session_id", required=True)
    workspace = _expand_path(_account_string(account, "codex_workspace", "~"))
    if not workspace.exists():
        raise SystemExit(f"codex_workspace does not exist: {workspace}")
    paths = _codex_state_paths(account, account.get("_preset") or "default")
    paths["state_file"].parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    home = str(Path.home())
    env["PATH"] = f"{home}/.local/bin:{home}/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"
    codex_args = _account_string_list(account, "codex_args", DEFAULT_CODEX_ARGS)
    prompt = _codex_prompt_for_slack(account, event_info)
    completed = None
    try:
        with tempfile.TemporaryDirectory(prefix="codex-", dir=str(paths["state_file"].parent)) as tmp_dir:
            output_path = Path(tmp_dir) / "last-message.txt"
            command = [
                "codex",
                "exec",
                "resume",
                session_id,
                *codex_args,
                "--output-last-message",
                str(output_path),
                "-",
            ]
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=_account_int(account, "codex_timeout_seconds", 900) + 60,
                check=False,
            )
            reply = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("codex exec resume failed") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SystemExit(f"codex exec resume failed: {detail or completed.returncode}")
    if not reply:
        reply = (completed.stdout or "").strip()
    if not reply:
        raise SystemExit("codex exec resume returned empty reply")
    return reply


def _truncate_for_slack(text, account):
    max_chars = _account_int(account, "slack_reply_max_chars", 39000)
    if len(text) <= max_chars:
        return text
    suffix = "\n\n[truncated]"
    return text[: max(1, max_chars - len(suffix))].rstrip() + suffix


def _mark_event_read(account, event_info):
    token = _direct_token(account, ("user_token", "bot_token"))
    if not isinstance(token, str) or not token.strip():
        return
    channel_id = event_info.get("channel_id")
    ts = event_info.get("ts")
    if not channel_id or not ts:
        return
    slack_request(
        "conversations.mark",
        {"channel": channel_id, "ts": ts},
        token.strip(),
        use_form=True,
        allow_error=True,
    )


def _send_codex_reply(account, event_info, reply):
    should_respond, reply_text = _parse_codex_reply_directive(
        reply,
        require_directive=_codex_requires_structured_reply(account),
    )
    if not should_respond:
        return None
    token = (
        _direct_token(account, ("user_token",))
        if event_info.get("kind") in {"user_direct_message", "user_mention"}
        else None
    )
    if not isinstance(token, str) or not token.strip():
        token = resolve_token(account)
    else:
        token = token.strip()
    thread_ts = event_info.get("thread_ts")
    text = _truncate_for_slack(reply_text, account)
    return send_post(token, event_info["channel_id"], text, thread_ts=thread_ts)


def _handle_socket_event(account, preset, event_info):
    raw_event = event_info.get("raw") or {}
    with _codex_lock(account, preset) as acquired:
        if not acquired:
            send_post(
                resolve_token(account),
                event_info["channel_id"],
                "Codex is still working on the previous Slack request.",
                thread_ts=event_info.get("thread_ts"),
            )
            return False
        if not _claim_event(account, preset, raw_event):
            return False
        try:
            reply = codex_resume_for_slack(account, event_info)
            reply_ts = _send_codex_reply(account, event_info, reply)
            _mark_event_read(account, event_info)
            _mark_codex_processed(account, preset, raw_event, reply_ts)
            _codex_log(account, preset, f"processed channel={event_info['channel_id']} ts={event_info.get('ts')}")
            return True
        except SystemExit as exc:
            message = f"Codex run failed: {exc}"
            _mark_codex_error(account, preset, str(exc))
            _codex_log(account, preset, message)
            send_post(
                resolve_token(account),
                event_info["channel_id"],
                message,
                thread_ts=event_info.get("thread_ts"),
            )
            return False


def _event_info_from_dm_entry(entry):
    if entry.get("surface") != "dm":
        return None
    message = entry.get("message") or {}
    sender = entry.get("sender") or {}
    channel_id = entry.get("channel_id") or entry.get("dm_id")
    ts = message.get("ts")
    user_id = sender.get("id") or message.get("user")
    if not channel_id or not ts or not user_id:
        return None
    return {
        "kind": "user_direct_message",
        "channel_id": channel_id,
        "user_id": user_id,
        "text": message_text(message),
        "thread_ts": message.get("thread_ts"),
        "ts": ts,
        "raw": {
            "type": "message",
            "channel": channel_id,
            "user": user_id,
            "text": message_text(message),
            "ts": ts,
            "event_ts": ts,
        },
    }


def _process_user_dm_entries(account, preset, entries):
    processed = 0
    for entry in sorted(entries, key=lambda item: item.get("sort_ts") or 0):
        event_info = _event_info_from_dm_entry(entry)
        if not event_info:
            continue
        if _handle_socket_event(account, preset, event_info):
            processed += 1
    return processed


def user_dm_scan_once(account, preset, *, unread_only=True):
    token = resolve_list_token(account)
    auth_data = auth_test(token)
    self_user_id = auth_data.get("user_id")
    if not self_user_id:
        raise SystemExit("Unable to determine the current Slack user.")
    limit = _account_int(account, "codex_user_dm_scan_limit", 10)
    contacts = contacts_for_account({}, account)
    entries = search_dms(
        contacts,
        token,
        limit,
        "unread" if unread_only else "all",
        self_user_id,
        False,
    )
    if entries is None:
        entries = []
        for dm_info in get_all_dm_infos(token):
            entries.extend(
                _collect_messages(
                    dm_info,
                    token,
                    limit,
                    "unread" if unread_only else "all",
                    self_user_id,
                )
            )
    selected_entries = entries[:limit]
    try:
        _event_cache_store_entries(_event_cache_db_path(account, preset), selected_entries)
    except Exception as exc:
        _codex_log(account, preset, f"event cache scan error: {exc}")
    return _process_user_dm_entries(account, preset, selected_entries)


def _state_float(state, key, default=0.0):
    try:
        return float(state.get(key) or default)
    except (TypeError, ValueError):
        return default


def _user_mention_event_from_match(match, token, self_user_id):
    channel = match.get("channel") if isinstance(match.get("channel"), dict) else {}
    channel_id = channel.get("id") or match.get("channel_id")
    ts = str(match.get("ts") or "")
    if not channel_id or not ts:
        return None
    try:
        message = _hydrate_message(channel_id, ts, token)
    except SystemExit:
        message = None
    message = message or {
        "ts": ts,
        "user": match.get("user"),
        "text": match.get("text") or "",
    }
    user_id = message.get("user") or match.get("user")
    if not user_id or user_id == self_user_id:
        return None
    if message.get("bot_id") or message.get("bot_profile") or message.get("subtype"):
        return None
    text = message_text(message)
    if self_user_id and f"<@{self_user_id}>" not in text:
        return None
    return {
        "kind": "user_mention",
        "channel_id": channel_id,
        "user_id": user_id,
        "text": text,
        "thread_ts": message.get("thread_ts") or ts,
        "ts": ts,
        "raw": {
            "type": "message",
            "channel": channel_id,
            "user": user_id,
            "text": text,
            "ts": ts,
            "event_ts": ts,
        },
    }


def user_mention_scan_once(account, preset):
    token = resolve_list_token(account)
    auth_data = auth_test(token)
    self_user_id = auth_data.get("user_id")
    if not self_user_id:
        raise SystemExit("Unable to determine the current Slack user.")
    paths = _codex_state_paths(account, preset)
    state = _read_state(paths["state_file"])
    since = _state_float(state, "user_mention_scan_after_ts", time.time())
    limit = _account_int(account, "codex_user_mention_scan_limit", 20)
    payload = slack_request(
        "search.messages",
        {
            "query": f"<@{self_user_id}>",
            "sort": "timestamp",
            "sort_dir": "desc",
            "count": str(max(20, min(100, limit))),
        },
        token,
        http_method="GET",
        allow_error=True,
    )
    if payload.get("ok") is not True:
        error = payload.get("error") or "unknown_error"
        raise SystemExit(f"Slack API error (search.messages): {error}")
    matches = (payload.get("messages") or {}).get("matches", []) or []
    events = []
    max_seen = since
    for match in matches:
        try:
            ts_value = float(match.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        max_seen = max(max_seen, ts_value)
        if ts_value <= since:
            continue
        event_info = _user_mention_event_from_match(match, token, self_user_id)
        if event_info:
            events.append((ts_value, event_info))
    processed = 0
    for _, event_info in sorted(events, key=lambda item: item[0]):
        if _handle_socket_event(account, preset, event_info):
            processed += 1
    state = _read_state(paths["state_file"])
    state["user_mention_scan_after_ts"] = max(max_seen, time.time())
    _write_state(paths["state_file"], state)
    return processed


def _user_dm_poll_loop(account, preset, stop_event):
    if account.get("codex_user_dm_watch") is False:
        _codex_log(account, preset, "user DM watcher disabled")
        return
    interval = max(5, _account_int(account, "codex_user_dm_poll_seconds", 10))
    _codex_log(account, preset, f"user DM watcher started interval={interval}s")
    while not stop_event.is_set():
        try:
            processed = user_dm_scan_once(account, preset, unread_only=True)
            processed += user_mention_scan_once(account, preset)
            if processed:
                _codex_log(account, preset, f"user_scan_processed={processed}")
        except SystemExit as exc:
            _mark_codex_error(account, preset, str(exc))
            _codex_log(account, preset, f"user DM watcher error: {exc}")
        except Exception as exc:
            _mark_codex_error(account, preset, str(exc))
            _codex_log(account, preset, f"user DM watcher error: {exc}")
        stop_event.wait(interval)


def _socket_loop(account, preset, *, once=False):
    app_token = resolve_app_token(account)
    bot_token = resolve_token(account)
    auth_data = auth_test(bot_token)
    bot_user_id = auth_data.get("user_id") or ""
    event_cache_context = _event_cache_socket_context(account, preset, _codex_log)
    socket = _open_socket_mode_connection(app_token)
    timeout_seconds = _account_int(account, "socket_timeout_seconds", 70)
    socket.settimeout(timeout_seconds)
    processed = 0
    try:
        while True:
            try:
                raw = socket.recv()
            except Exception as exc:
                if once:
                    _codex_log(account, preset, f"once timeout/no event: {exc}")
                    return processed
                raise
            if not raw:
                continue
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                continue
            envelope_type = envelope.get("type")
            if envelope_type == "hello":
                _codex_log(account, preset, "socket connected")
                continue
            if envelope_type == "disconnect":
                _codex_log(account, preset, f"socket disconnect: {envelope.get('reason') or '-'}")
                return processed
            if "envelope_id" in envelope:
                _ack_socket_envelope(socket, envelope)
            if envelope_type != "events_api":
                continue
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
            _event_cache_store_socket_payload_from_context(
                account,
                preset,
                payload,
                event_cache_context,
                _codex_log,
            )
            event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            event_info = _eligible_slack_event(event, bot_user_id)
            if not event_info:
                continue
            if _handle_socket_event(account, preset, event_info):
                processed += 1
            if once and processed:
                return processed
    finally:
        try:
            socket.close()
        except Exception:
            pass


def codex_once(account, preset):
    account = dict(account)
    account["_preset"] = preset
    processed = _socket_loop(account, preset, once=True)
    print(f"codex_once processed={processed}")
    return 0


def codex_scan(account, preset):
    account = dict(account)
    account["_preset"] = preset
    processed = user_dm_scan_once(account, preset, unread_only=True)
    print(f"codex_scan processed={processed}")
    return 0


def codex_service(account, preset):
    account = dict(account)
    account["_preset"] = preset
    _codex_log(account, preset, "service started")
    stop_event = threading.Event()
    poller = threading.Thread(
        target=_user_dm_poll_loop,
        args=(account, preset, stop_event),
        daemon=True,
    )
    poller.start()
    while True:
        try:
            _socket_loop(account, preset, once=False)
        except SystemExit as exc:
            _mark_codex_error(account, preset, str(exc))
            _codex_log(account, preset, f"service error: {exc}")
            time.sleep(5)
        except Exception as exc:
            _mark_codex_error(account, preset, str(exc))
            _codex_log(account, preset, f"service error: {exc}")
            time.sleep(5)


def codex_status(account, preset):
    paths = _codex_state_paths(account, preset)
    state = _read_state(paths["state_file"])
    state.update(
        {
            "config": get_config_path(),
            "log": str(paths["log_file"]),
            "state": str(paths["state_file"]),
            "workspace": _account_string(account, "codex_workspace", "~"),
            "session_id": _account_string(account, "codex_session_id"),
            "has_app_token": bool(_has_token(account, "app") or _read_token_file(DEFAULT_APP_TOKEN_FILE)),
            "has_bot_token": _has_token(account, "bot"),
        }
    )
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def codex_reset_state(account, preset):
    paths = _codex_state_paths(account, preset)
    _write_state(paths["state_file"], {})
    _codex_log(account, preset, "state reset")
    print("state reset")
    return 0


def _codex_unit_name(preset):
    return f"slack-codex-{_safe_preset_slug(preset)}"


def _systemd_unit_dir():
    return Path.home() / ".config" / "systemd" / "user"


def _codex_unit_path(preset):
    return _systemd_unit_dir() / f"{_codex_unit_name(preset)}.service"


def _systemctl_user(*args, check=True):
    result = subprocess.run(
        ["systemctl", "--user", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"systemctl --user {' '.join(args)} failed: {detail}")
    return result


def _systemd_user_service_active(unit):
    result = _systemctl_user("is-active", unit, check=False)
    return result.returncode == 0 and (result.stdout or "").strip() == "active"


def write_codex_unit(preset):
    unit_path = _codex_unit_path(preset)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        "\n".join(
            [
                "[Unit]",
                f"Description=Slack preset {preset} to Codex event bridge",
                "After=network-online.target",
                "",
                "[Service]",
                "Type=simple",
                "Environment=PYTHONUNBUFFERED=1",
                f"ExecStart=%h/.local/bin/slack {preset} codex service",
                "Restart=always",
                "RestartSec=5",
                "Nice=5",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    if shutil.which("systemd-analyze") is not None:
        result = subprocess.run(
            ["systemd-analyze", "--user", "verify", str(unit_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SystemExit(f"systemd unit validation failed: {detail}")
    return unit_path


def codex_install_service(preset):
    write_codex_unit(preset)
    unit = f"{_codex_unit_name(preset)}.service"
    _systemctl_user("daemon-reload")
    _systemctl_user("enable", "--now", unit)
    _systemctl_user("restart", unit)
    print(f"service enabled: {unit}")
    return 0


def codex_disable_service(preset):
    write_codex_unit(preset)
    unit = f"{_codex_unit_name(preset)}.service"
    _systemctl_user("disable", "--now", unit, check=False)
    _systemctl_user("daemon-reload")
    print(f"service disabled: {unit}")
    return 0


def codex_service_status(preset):
    result = subprocess.run(
        ["systemctl", "--user", "status", f"{_codex_unit_name(preset)}.service", "--no-pager"],
        check=False,
        text=True,
    )
    return result.returncode


def codex_service_logs(preset, lines=80):
    result = subprocess.run(
        ["journalctl", "--user", "-u", f"{_codex_unit_name(preset)}.service", "-n", str(lines), "--no-pager"],
        check=False,
        text=True,
    )
    return result.returncode


def print_codex_help():
    print(
        """Usage:
  slack <preset> codex once
  slack <preset> codex scan
  slack <preset> codex service
  slack <preset> codex ti
  slack <preset> codex td
  slack <preset> codex st
  slack <preset> codex logs [lines]
  slack <preset> codex status
  slack <preset> codex reset-state"""
    )
    return 0


def _config_path() -> Path:
    return Path(get_config_path())


def _dispatch(argv: list[str]) -> int:
    args = parse_args(argv)
    config_path = get_config_path(args["config"])

    if args["command"] in {"cfg", "conf"}:
        return open_config_in_editor(
            lambda: Path(config_path),
            bootstrap_text=CONFIG_BOOTSTRAP_TEXT,
        )

    config = load_config(config_path)

    if args["command"] == "auth":
        if args["auth_list"]:
            list_account_presets(config)
            return 0
        configure_account(
            config_path,
            config,
            args["auth_preset"],
            args["auth_bot_token"],
            args["auth_user_token"],
            args["auth_app_token"],
            args["auth_name"],
            args["auth_import"],
        )
        return 0

    preset, account = select_account(config, args["preset"])
    contacts = contacts_for_account(config, account)

    if args["command"] == "ac":
        label = (args["label"] or "").strip()
        email = (args["email"] or "").strip()
        if not label:
            raise SystemExit("Label cannot be empty.")
        if "@" not in email:
            raise SystemExit("Use: slack ac <label> <email>")
        save_contact(config, preset, label, email)
        save_config(config_path, config)
        print(f"Saved contact '{label}' -> {email}")
        return 0

    if not args["command"]:
        return 0

    if args["command"] == "codex":
        action = args["codex_action"]
        if action == "help":
            return print_codex_help()
        if action == "once":
            return codex_once(account, preset)
        if action == "scan":
            return codex_scan(account, preset)
        if action == "service":
            return codex_service(account, preset)
        if action == "ti":
            return codex_install_service(preset)
        if action == "td":
            return codex_disable_service(preset)
        if action == "st":
            return codex_service_status(preset)
        if action == "logs":
            return codex_service_logs(preset, args["codex_lines"])
        if action == "status":
            return codex_status(account, preset)
        if action == "reset-state":
            return codex_reset_state(account, preset)
        raise SystemExit("Use: slack <preset> codex once|scan|service|ti|td|st|logs|status|reset-state")

    if args["command"] == "events":
        action = args["events_action"]
        if action == "help":
            return print_events_help()
        if action == "sync":
            events_sync_once(account, preset)
            return 0
        if action == "once":
            return events_once(account, preset)
        if action == "service":
            return events_service(account, preset)
        if action == "ti":
            return events_install_service(preset)
        if action == "td":
            return events_disable_service(preset)
        if action == "st":
            return events_service_status(preset)
        if action == "logs":
            return events_service_logs(preset, args["events_lines"])
        if action == "status":
            return events_status(account, preset)
        if action == "reset-cache":
            return events_reset_cache(account, preset)
        raise SystemExit("Use: slack <preset> events sync|once|service|ti|td|st|logs|status|reset-cache")

    if args["command"] == "ls" and args["ls_registry"]:
        list_registered_contacts(contacts)
        return 0

    if args["command"] in {"su", "u"}:
        token = resolve_token(account)
        auth_test(token)
        search_users_and_contacts(contacts, token, args["query"])
        return 0

    if args["command"] == "ls":
        token = resolve_list_token(account)
        auth_data = auth_test(token)
        self_user_id = auth_data.get("user_id")
        if not self_user_id:
            raise SystemExit("Unable to determine the current Slack user.")
        list_dms(
            contacts,
            token,
            args["ls_limit"],
            args["ls_filter"],
            self_user_id,
            args["open_mode"],
            label=args["ls_label"],
            sender_filter=args["ls_from"],
            contains_filter=args["ls_contains"],
            time_limit=args["ls_time_limit"],
            cache_path=_event_cache_db_path(account, preset),
        )
        return 0

    if args["command"] == "tui":
        token = resolve_list_token(account)
        auth_data = auth_test(token)
        self_user_id = auth_data.get("user_id")
        if not self_user_id:
            raise SystemExit("Unable to determine the current Slack user.")
        run_slack_tui(token, self_user_id, cache_path=_event_cache_db_path(account, preset))
        return 0

    token = resolve_token(account)
    auth_data = auth_test(token)

    if args["command"] == "o":
        self_user_id = auth_data.get("user_id")
        if not self_user_id:
            raise SystemExit("Unable to determine the current Slack user.")
        open_dm_messages(args["recipient"], token, self_user_id)
        return 0

    if args["command"] == "mra":
        mark_all_unread_dms_as_read(contacts, token)
        return 0

    if args["command"] == "df":
        download_dm_file(args["recipient"], args["file_id"], args["output_path"], token)
        return 0

    if args["command"] == "sc":
        clear_stale_conversations(token)
        return 0

    if args["command"] == "post":
        direct_token = resolve_direct_post_token(account, token)
        lookup_token = resolve_lookup_token(account, direct_token)
        target = resolve_post_target(args["recipient"], contacts, token, lookup_token, direct_token)
        post_token = direct_token if target["kind"] in {"email", "user"} else token
        channel_id = target["channel_id"]
        ts = send_post(post_token, channel_id, args["message"])
        uploaded = send_attachments(channel_id, ts, args["paths"], post_token)
        details = [
            "posted",
            f"target={args['recipient']}",
            f"kind={target['kind']}",
            f"channel={channel_id}",
        ]
        if ts:
            details.append(f"ts={ts}")
        if uploaded:
            details.append(f"files={','.join(uploaded)}")
        print(" ".join(details))
        return 0

    if args["command"] == "reply":
        channel_id, message_ts = parse_message_id(args["recipient"])
        thread_ts = resolve_reply_thread_ts(channel_id, message_ts, token)
        ts = send_post(token, channel_id, args["message"], thread_ts=thread_ts)
        uploaded = send_attachments(channel_id, thread_ts, args["paths"], token)
        details = [
            "replied",
            f"message_id={args['recipient']}",
            f"channel={channel_id}",
            f"thread_ts={thread_ts}",
        ]
        if ts:
            details.append(f"ts={ts}")
        if uploaded:
            details.append(f"files={','.join(uploaded)}")
        print(" ".join(details))
        return 0

    raise SystemExit(_top_level_usage())


APP_SPEC = AppSpec(
    app_name="slack",
    version=__version__,
    help_text=HELP_TEXT,
    install_script_path=INSTALL_SCRIPT,
    no_args_mode="help",
    config_path_factory=_config_path,
    config_bootstrap_text=CONFIG_BOOTSTRAP_TEXT,
)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    return run_app(APP_SPEC, args, _dispatch)


if __name__ == "__main__":
    raise SystemExit(main())
