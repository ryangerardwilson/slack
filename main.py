import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import zipfile
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
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]+$")
DEFAULT_BOT_TOKEN_FILE = "~/.openclaw/credentials/slack-bot-token"
DEFAULT_USER_TOKEN_FILE = "~/.openclaw/credentials/slack-user-token"
DEFAULT_LIST_LIMIT = 10
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
  # slack ac <label> <email>
  slack ac mom mom@example.com
  slack ac boss boss@company.com

  edit the saved-contact config directly in your editor
  # slack conf
  slack conf

  send a direct message from the configured Slack app token, with any number of file or directory attachments
  # slack dm <contact_label|email> <message> [path...]
  slack dm mom "hello"
  slack dm boss@company.com "latest draft" ~/Downloads/draft.pdf
  slack dm design "assets attached" ~/Downloads/mock.png ~/Projects/site/export ~/Downloads/spec.pdf

  download a file attachment from a DM by dm_id and file_id
  # slack df <dm_id> <file_id> [output_path]
  slack df D0466D63H7B F0AH0LD4133

  open a DM or exact message id, mark it read, show text, download files, and print code blocks
  # slack o <dm_id|message_id>
  slack o D0466D63H7B
  slack o D0466D63H7B:1712764800.000100

  list Slack message history with Gmail-style filters, surface labels, and attached file ids
  # slack ls [label] [-ur|-r] [-o] [-l <limit>] [-f <from>] [-c <contains>] [-tl <time_limit>]
  slack ls
  slack ls 10
  slack ls md 10
  slack ls -l 20
  slack ls -f maanas -tl 2w -l 10
  slack ls -c invoice -tl "jan 2025" -l 20
  slack ls -ur 10
  slack ls md -r 10
  slack ls md -o 5

  list all registered contact labels
  # slack ls rc
  slack ls rc

  search saved contacts and Slack workspace users
  # slack su <query>
  slack su rohan
  slack su "rohan choudhary"

  clear stale conversations and bot-like conversations
  # slack sc
  slack sc

  mark all unread saved-contact direct messages as read
  # slack mra
  slack mra
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


def _requests():
    import requests

    return requests


def _ls_usage():
    return (
        "Use: slack ls rc | slack ls [label] [-ur|-r] [-o] "
        "[-l <limit>] [-f <from>] [-c <contains>] [-tl <time_limit>]"
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
        "config": None,
        "version": False,
        "upgrade": False,
    }

    if not argv:
        return args

    index = 0
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
            raise SystemExit(
                "Use: slack ac <label> <email> | slack conf | slack dm <contact_label|email> <message> [path...]"
            )

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
        if token == "dm":
            if len(remaining) < 2:
                raise SystemExit(
                    "Use: slack dm <contact_label|email> <message> [path...]"
                )
            args["recipient"] = remaining[0]
            args["message"] = remaining[1]
            args["paths"] = remaining[2:]
            return args
        if token == "df":
            if len(remaining) < 2 or len(remaining) > 3:
                raise SystemExit("Use: slack df <dm_id> <file_id> [output_path]")
            args["recipient"] = remaining[0]
            args["file_id"] = remaining[1]
            if len(remaining) == 3:
                args["output_path"] = remaining[2]
            return args
        if token == "o":
            if len(remaining) != 1:
                raise SystemExit("Use: slack o <dm_id>")
            args["recipient"] = remaining[0]
            args["open_mode"] = True
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
        raise SystemExit(
            "Use: slack ac <label> <email> | slack su <query> | slack conf | slack dm <contact_label|email> <message> [path...] | slack df <dm_id> <file_id> [output_path] | slack o <dm_id|message_id> | slack ls rc | slack ls [label] [-ur|-r] [-o] [-l <limit>] | slack sc | slack mra"
        )

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


def resolve_contact_email(recipient, contacts):
    if recipient in contacts:
        return contacts[recipient]
    if recipient and "@" in recipient:
        return recipient.strip()
    raise SystemExit("Recipient must be a contact label or email.")


def lookup_user_id_by_email(email, token):
    data = slack_request(
        "users.lookupByEmail",
        {"email": email},
        token,
        http_method="GET",
    )
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


def send_dm(token, user_id, text):
    channel_id = open_dm(user_id, token)
    data = slack_request(
        "chat.postMessage", {"channel": channel_id, "text": text}, token
    )
    message = data.get("message") or {}
    return channel_id, message.get("ts")


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


def message_files(message):
    collected = []
    seen = set()

    for file_payload in message.get("files") or []:
        file_id = file_payload.get("id")
        if file_id and file_id in seen:
            continue
        if file_id:
            seen.add(file_id)
        collected.append(file_payload)

    for attachment in message.get("attachments") or []:
        for file_payload in attachment.get("files") or []:
            file_id = file_payload.get("id")
            if file_id and file_id in seen:
                continue
            if file_id:
                seen.add(file_id)
            collected.append(file_payload)

    return collected


def summarize_files(message):
    files = message_files(message)
    rendered = []
    for file in files:
        file_id = file.get("id")
        if not file_id:
            continue
        name = file.get("name") or "unnamed"
        rendered.append(f"{file_id}:{name}")
    return ", ".join(rendered) if rendered else "-"


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
        "info": {},
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
    name = file_payload.get("name") or file_payload.get("title") or file_payload.get("id") or "attachment"
    filename = f"{dm_id}-{file_payload.get('id')}-{name}"
    return os.path.abspath(os.path.expanduser(filename))


def _message_details(message, dm_id, token):
    downloads = []
    code_blocks = []
    for file_payload in message_files(message):
        download_url = file_payload.get("url_private_download")
        if download_url:
            destination = _download_destination(dm_id, file_payload)
            _download_file_to_path(download_url, destination, token)
            downloads.append(
                {
                    "id": file_payload.get("id") or "-",
                    "name": file_payload.get("name") or "attachment",
                    "path": destination,
                }
            )

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
            for file_info in downloads:
                print(style_help(f"{'file':<8}: {file_info['id']} {file_info['path']}"))
        else:
            print(style_help(f"{'file':<8}: -"))

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
            ("files", summarize_files(item["message"])),
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
):
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
            marked += 1
        print(f"ls_opened messages={len(selected)} marked_conversations={marked}")
        return

    print_sections([_list_entry_fields(item) for item in selected])


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
        print(f"opened_and_marked_read dm_id={dm_id} ts={latest_ts}")


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

                print(f"downloaded dm_id={dm_id} file_id={file_id} path={destination}")
                return

        cursor = (
            (history.get("response_metadata") or {}).get("next_cursor") or ""
        ).strip()
        if not cursor:
            break

    raise SystemExit(f"File not found in dm_id={dm_id}: {file_id}")


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


def _config_path() -> Path:
    return Path(get_config_path())


def _dispatch(argv: list[str]) -> int:
    args = parse_args(argv)
    config_path = get_config_path(args["config"])

    if args["command"] in {"cfg", "conf"}:
        return open_config_in_editor(lambda: Path(config_path))

    config = load_config(config_path)
    contacts = normalize_contacts(config)

    if args["command"] == "ac":
        label = (args["label"] or "").strip()
        email = (args["email"] or "").strip()
        if not label:
            raise SystemExit("Label cannot be empty.")
        if "@" not in email:
            raise SystemExit("Use: slack ac <label> <email>")
        contacts[label] = email
        config["contacts"] = contacts
        if "user_labels" in config:
            del config["user_labels"]
        save_config(config_path, config)
        print(f"Saved contact '{label}' -> {email}")
        return 0

    if not args["command"]:
        return 0

    if args["command"] == "ls" and args["ls_registry"]:
        list_registered_contacts(contacts)
        return 0

    if args["command"] in {"su", "u"}:
        token = resolve_token(config)
        auth_test(token)
        search_users_and_contacts(contacts, token, args["query"])
        return 0

    if args["command"] == "ls":
        token = resolve_list_token(config)
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
        )
        return 0

    token = resolve_token(config)
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

    if args["command"] != "dm":
        raise SystemExit(
            "Use: slack ac <label> <email> | slack su <query> | slack conf | slack dm <contact_label|email> <message> [path...] | slack df <dm_id> <file_id> [output_path] | slack o <dm_id|message_id> | slack ls rc | slack ls [label] [-ur|-r] [-o] [-l <limit>] | slack sc | slack mra"
        )

    recipient_email = resolve_contact_email(args["recipient"], contacts)
    user_id = lookup_user_id_by_email(recipient_email, token)
    channel_id, ts = send_dm(token, user_id, args["message"])
    uploaded = send_attachments(channel_id, ts, args["paths"], token)

    if uploaded:
        print(
            f"DM sent. email={recipient_email} channel={channel_id} ts={ts} files={','.join(uploaded)}"
        )
        return 0
    if ts:
        print(f"DM sent. email={recipient_email} channel={channel_id} ts={ts}")
        return 0
    print(f"DM sent. email={recipient_email} channel={channel_id}")
    return 0


APP_SPEC = AppSpec(
    app_name="slack",
    version=__version__,
    help_text=HELP_TEXT,
    install_script_path=INSTALL_SCRIPT,
    no_args_mode="help",
    config_path_factory=_config_path,
    config_bootstrap_text="{}\n",
)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    return run_app(APP_SPEC, args, _dispatch)


if __name__ == "__main__":
    raise SystemExit(main())
