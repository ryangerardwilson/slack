import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import requests

try:
    from _version import __version__
except Exception:
    __version__ = "0.0.0"


INSTALL_URL = "https://raw.githubusercontent.com/ryangerardwilson/slack/main/install.sh"
LATEST_RELEASE_API = "https://api.github.com/repos/ryangerardwilson/slack/releases/latest"

USER_TOKEN_PREFIXES = ("xoxp-", "xoxc-")
BOT_TOKEN_PREFIX = "xoxb-"
HELP_TEXT = """Slack CLI

flags:
  slack -h
  # show this help
  slack -v
  # print the installed version
  slack -u
  # upgrade to the latest release

features:
  # save a contact label
  # slack ac <label> <email>
  slack ac mom mom@example.com
  slack ac boss boss@company.com

  # send a direct message, with an optional file and optional zipped directory
  # slack dm <contact_label|email> <message> [file_path] [dir_path]
  slack dm mom "hello"
  slack dm boss@company.com "latest draft" ~/Downloads/draft.pdf
  slack dm design "assets attached" ~/Downloads/mock.png ~/Projects/site/export

  # list unread direct messages or unread mentions
  # slack ls -dms [-ur|-r] <number> | slack ls -mnts
  slack ls -dms 10
  slack ls -dms -ur 10
  slack ls -dms -r 10
  slack ls -mnts

  # clear stale conversations and bot-like conversations
  # slack sc
  slack sc
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
            and "@" in value
        ):
            cleaned[key.strip()] = value.strip()
    return cleaned


def style_help(text):
    if sys.stdout.isatty() and not os.getenv("NO_COLOR"):
        return f"\033[38;5;245m{text}\033[0m"
    return text


def print_help():
    print(style_help(HELP_TEXT.rstrip()))


def parse_args(argv):
    args = {
        "command": None,
        "label": None,
        "email": None,
        "recipient": None,
        "message": None,
        "file_path": None,
        "dir_path": None,
        "ls_mode": None,
        "ls_filter": "all",
        "ls_limit": None,
        "config": None,
        "version": False,
        "upgrade": False,
    }

    if not argv:
        return args

    index = 0
    while index < len(argv):
        token = argv[index]

        if token == "-h":
            print_help()
            raise SystemExit(0)
        if token == "-v":
            args["version"] = True
            index += 1
            continue
        if token == "-u":
            args["upgrade"] = True
            index += 1
            continue
        if token == "-cfg":
            if index + 1 >= len(argv):
                raise SystemExit("Use: slack -cfg <config_path>")
            args["config"] = argv[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            raise SystemExit(f"Unknown flag: {token}")

        if args["command"] is not None:
            raise SystemExit("Use: slack ac <label> <email> | slack dm <contact_label|email> <message> [file_path] [dir_path]")

        args["command"] = token
        remaining = argv[index + 1 :]
        if token == "ac":
            if len(remaining) != 2:
                raise SystemExit("Use: slack ac <label> <email>")
            args["label"], args["email"] = remaining
            return args
        if token == "dm":
            if len(remaining) < 2 or len(remaining) > 4:
                raise SystemExit(
                    "Use: slack dm <contact_label|email> <message> [file_path] [dir_path]"
                )
            args["recipient"] = remaining[0]
            args["message"] = remaining[1]
            extra_paths = remaining[2:]
            for path in extra_paths:
                expanded = os.path.expanduser(path)
                if os.path.isdir(expanded):
                    if args["dir_path"] is not None:
                        raise SystemExit("Use at most one directory path.")
                    args["dir_path"] = path
                else:
                    if args["file_path"] is not None:
                        raise SystemExit("Use at most one file path.")
                    args["file_path"] = path
            return args
        if token == "ls":
            if not remaining:
                raise SystemExit("Use: slack ls -dms [-ur|-r] <number> | slack ls -mnts")
            args["ls_mode"] = remaining[0]
            if args["ls_mode"] == "-mnts":
                if len(remaining) != 1:
                    raise SystemExit("Use: slack ls -mnts")
                return args
            if args["ls_mode"] != "-dms":
                raise SystemExit("Use: slack ls -dms [-ur|-r] <number> | slack ls -mnts")
            if len(remaining) == 2:
                limit_token = remaining[1]
                args["ls_filter"] = "all"
            elif len(remaining) == 3 and remaining[1] in ("-ur", "-r"):
                args["ls_filter"] = "unread" if remaining[1] == "-ur" else "read"
                limit_token = remaining[2]
            else:
                raise SystemExit("Use: slack ls -dms [-ur|-r] <number>")
            try:
                args["ls_limit"] = int(limit_token)
            except ValueError:
                raise SystemExit("Use: slack ls -dms [-ur|-r] <number>")
            if args["ls_limit"] <= 0:
                raise SystemExit("Number must be greater than 0.")
            return args
        if token == "sc":
            if remaining:
                raise SystemExit("Use: slack sc")
            return args
        raise SystemExit(
            "Use: slack ac <label> <email> | slack dm <contact_label|email> <message> [file_path] [dir_path] | slack ls -dms [-ur|-r] <number> | slack ls -mnts | slack sc"
        )

    return args


def _version_tuple(version):
    if not version:
        return (0,)
    version = version.strip()
    if version.startswith("v"):
        version = version[1:]
    parts = []
    for segment in version.split("."):
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def _is_version_newer(candidate, current):
    cand_tuple = _version_tuple(candidate)
    curr_tuple = _version_tuple(current)
    length = max(len(cand_tuple), len(curr_tuple))
    cand_tuple += (0,) * (length - len(cand_tuple))
    curr_tuple += (0,) * (length - len(curr_tuple))
    return cand_tuple > curr_tuple


def _get_latest_version(timeout=5.0):
    try:
        request = Request(
            LATEST_RELEASE_API, headers={"User-Agent": "slack-updater"}
        )
        with urlopen(request, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError, TimeoutError):
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    tag = payload.get("tag_name") or payload.get("name")
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    return None


def _run_upgrade():
    try:
        curl = subprocess.Popen(
            ["curl", "-fsSL", INSTALL_URL],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("Upgrade requires curl", file=sys.stderr)
        return 1

    try:
        bash = subprocess.Popen(["bash"], stdin=curl.stdout)
        if curl.stdout is not None:
            curl.stdout.close()
    except FileNotFoundError:
        print("Upgrade requires bash", file=sys.stderr)
        curl.terminate()
        curl.wait()
        return 1

    bash_rc = bash.wait()
    curl_rc = curl.wait()

    if curl_rc != 0:
        stderr = (
            curl.stderr.read().decode("utf-8", errors="replace")
            if curl.stderr
            else ""
        )
        if stderr:
            sys.stderr.write(stderr)
        return curl_rc

    return bash_rc


def read_from_editor():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        temp_path = tmp.name

    try:
        editor = os.getenv("VISUAL") or os.getenv("EDITOR") or "vim"
        editor = editor.strip()
        editor_cmd = shlex.split(editor) if editor else ["vim"]
        if not editor_cmd:
            editor_cmd = ["vim"]
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


def resolve_token():
    token = get_env("SLACK_TOKEN")
    if not token:
        raise SystemExit("Missing SLACK_TOKEN env var.")
    if token.startswith(BOT_TOKEN_PREFIX):
        raise SystemExit("Bot tokens are not supported. Use a user token.")
    if not token.startswith(USER_TOKEN_PREFIXES):
        raise SystemExit("SLACK_TOKEN must be a user token (xoxp- or xoxc-).")
    return token


def slack_request(method, payload, token, use_form=False, http_method="POST"):
    url = f"https://slack.com/api/{method}"
    headers = {"Authorization": f"Bearer {token}"}
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
    if response.status_code != 200:
        raise SystemExit(
            f"Slack API HTTP {response.status_code}: {response.text.strip()}"
        )
    try:
        data = response.json()
    except json.JSONDecodeError:
        raise SystemExit("Slack API returned invalid JSON.")
    if not data.get("ok"):
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
    if data.get("bot_id"):
        raise SystemExit("Token belongs to a bot. Use a user token.")
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


def send_attachments(channel_id, thread_ts, file_path, dir_path, token):
    uploaded = []

    if file_path:
        expanded = expand_existing_path(file_path, "file")
        filename = os.path.basename(expanded)
        file_id = _upload_external_file(
            channel_id, thread_ts, expanded, filename, token
        )
        uploaded.append(filename)

    archive_path = None
    archive_name = None
    try:
        if dir_path:
            archive_path, archive_name = zip_directory(dir_path)
            file_id = _upload_external_file(
                channel_id, thread_ts, archive_path, archive_name, token
            )
            uploaded.append(archive_name)
    finally:
        if archive_path:
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


def format_ts(ts_value):
    try:
        dt = datetime.fromtimestamp(float(ts_value))
    except (TypeError, ValueError, OSError):
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def extract_ts(payload):
    latest = payload.get("latest")
    if isinstance(latest, dict):
        return latest.get("ts") or "0"
    if isinstance(latest, str):
        return latest
    return "0"


def print_sections(rows):
    for index, row in enumerate(rows, start=1):
        print(f"[{index}]-----")
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


def list_dms(contacts, token, limit, filter_mode):
    inverse_contacts = {email: label for label, email in contacts.items()}
    user_cache = {}
    info_cache = {}
    rows = []
    channels = list_api(
        "users.conversations",
        {"types": "im", "exclude_archived": "true", "limit": "200"},
        token,
    )
    for channel in channels:
            channel_id = channel.get("id")
            if not channel_id:
                continue

            if channel_id not in info_cache:
                info_data = slack_request(
                    "conversations.info",
                    {"channel": channel_id, "include_num_members": "false"},
                    token,
                    http_method="GET",
                )
                info_cache[channel_id] = info_data.get("channel") or {}

            info_channel = info_cache[channel_id]
            unread = info_channel.get("unread_count_display") or info_channel.get(
                "unread_count"
            ) or 0
            user_id = info_channel.get("user") or channel.get("user") or "-"
            if user_id not in user_cache:
                user_cache[user_id] = get_user_info(user_id, token)
            user = user_cache[user_id]
            profile = user.get("profile") or {}
            email = profile.get("email") or "-"
            if email == "-":
                continue
            is_unread = unread > 0
            if filter_mode == "unread" and not is_unread:
                continue
            if filter_mode == "read" and is_unread:
                continue
            display_name = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("name")
                or user_id
            )
            label = inverse_contacts.get(email, "-")
            latest = info_channel.get("latest") or {}
            latest_ts = latest.get("ts") if isinstance(latest, dict) else None
            latest_text = (
                compact_text(latest.get("text")) if isinstance(latest, dict) else "-"
            )

            rows.append(
                {
                    "sort_ts": float(extract_ts(info_channel)),
                    "row": [
                        ("label", label),
                        ("name", display_name),
                        ("email", email),
                        ("dm_id", channel_id),
                        ("user_id", user_id),
                        ("unread", str(unread)),
                        ("date", format_ts(latest_ts)),
                        ("latest", latest_text),
                    ],
                }
            )

    if not rows:
        if filter_mode == "unread":
            print("No unread DMs.")
        elif filter_mode == "read":
            print("No read DMs.")
        else:
            print("No DMs.")
        return

    rows.sort(key=lambda item: item["sort_ts"], reverse=True)
    selected = rows[:limit]
    selected.sort(key=lambda item: item["sort_ts"])
    print_sections([item["row"] for item in selected])


def list_unread_mentions(self_user_id, token):
    query = f'"<@{self_user_id}>" is:unread -from:<@{self_user_id}>'
    data = slack_request(
        "search.messages",
        {
            "query": query,
            "count": "20",
            "sort": "timestamp",
            "sort_dir": "desc",
        },
        token,
        http_method="GET",
    )
    matches = ((data.get("messages") or {}).get("matches")) or []
    if not matches:
        print("No unread mentions.")
        return

    rows = []
    for match in matches:
        channel = match.get("channel") or {}
        channel_name = channel.get("name") or channel.get("id") or "-"
        speaker = match.get("username") or match.get("user") or "-"
        rows.append(
            [
                ("channel", channel_name),
                ("speaker", speaker),
                ("ts", match.get("ts") or "-"),
                ("text", compact_text(match.get("text"))),
                ("link", match.get("permalink") or "-"),
            ]
        )

    print_sections(rows)


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


def main():
    args = parse_args(sys.argv[1:])

    if args["version"]:
        print(__version__)
        return

    if args["upgrade"]:
        if (
            args["command"]
            or args["recipient"]
            or args["message"]
            or args["label"]
            or args["email"]
            or args["file_path"]
            or args["dir_path"]
            or args["ls_mode"]
        ):
            raise SystemExit("Use -u by itself to upgrade.")

        latest = _get_latest_version()
        if latest is None:
            print(
                "Unable to determine latest version; attempting upgrade…",
                file=sys.stderr,
            )
            rc = _run_upgrade()
            sys.exit(rc)

        if (
            __version__
            and __version__ != "0.0.0"
            and not _is_version_newer(latest, __version__)
        ):
            print(f"Already running the latest version ({__version__}).")
            sys.exit(0)

        if __version__ and __version__ != "0.0.0":
            print(f"Upgrading from {__version__} to {latest}…")
        else:
            print(f"Upgrading to {latest}…")
        rc = _run_upgrade()
        sys.exit(rc)

    config_path = get_config_path(args["config"])
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
        return

    if not args["command"]:
        print_help()
        return

    token = resolve_token()
    auth_data = auth_test(token)

    if args["command"] == "ls":
        if args["ls_mode"] == "-dms":
            list_dms(contacts, token, args["ls_limit"], args["ls_filter"])
            return
        if args["ls_mode"] == "-mnts":
            self_user_id = auth_data.get("user_id")
            if not self_user_id:
                raise SystemExit("Unable to determine the current Slack user.")
            list_unread_mentions(self_user_id, token)
            return
        raise SystemExit("Use: slack ls -dms | slack ls -mnts")

    if args["command"] == "sc":
        clear_stale_conversations(token)
        return

    if args["command"] != "dm":
        raise SystemExit(
            "Use: slack ac <label> <email> | slack dm <contact_label|email> <message> [file_path] [dir_path] | slack ls -dms | slack ls -mnts | slack sc"
        )

    recipient_email = resolve_contact_email(args["recipient"], contacts)
    user_id = lookup_user_id_by_email(recipient_email, token)
    channel_id, ts = send_dm(token, user_id, args["message"])
    uploaded = send_attachments(
        channel_id, ts, args["file_path"], args["dir_path"], token
    )

    if uploaded:
        print(
            f"DM sent. email={recipient_email} channel={channel_id} ts={ts} files={','.join(uploaded)}"
        )
    elif ts:
        print(f"DM sent. email={recipient_email} channel={channel_id} ts={ts}")
    else:
        print(f"DM sent. email={recipient_email} channel={channel_id}")


if __name__ == "__main__":
    main()
