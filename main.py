import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from _version import __version__


INSTALL_URL = "https://raw.githubusercontent.com/ryangerardwilson/slack/main/install.sh"
LATEST_RELEASE_API = "https://api.github.com/repos/ryangerardwilson/slack/releases/latest"

USER_TOKEN_PREFIXES = ("xoxp-", "xoxc-")
BOT_TOKEN_PREFIX = "xoxb-"
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]+$")
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
  # slack cfg
  slack cfg

  send a direct message, with an optional file and optional zipped directory
  # slack dm <contact_label|email> <message> [file_path] [dir_path]
  slack dm mom "hello"
  slack dm boss@company.com "latest draft" ~/Downloads/draft.pdf
  slack dm design "assets attached" ~/Downloads/mock.png ~/Projects/site/export

  download a file attachment from a DM by dm_id and file_id
  # slack df <dm_id> <file_id> [output_path]
  slack df D0466D63H7B F0AH0LD4133

  open a DM, mark it read, show text, download files, and print code blocks
  # slack o <dm_id>
  slack o D0466D63H7B

  list saved-contact direct message history with attached files
  # slack ls [label] [-ur|-r] [-o] <number>
  slack ls 10
  slack ls md 10
  slack ls -ur 10
  slack ls md -r 10
  slack ls md -o 5

  list all registered contact labels
  # slack ls rc
  slack ls rc

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


def style_help(text):
    if sys.stdout.isatty() and not os.getenv("NO_COLOR"):
        return f"\033[38;5;245m{text}\033[0m"
    return text


def print_help():
    print(style_help(HELP_TEXT.rstrip()))


def _requests():
    import requests

    return requests


def parse_args(argv):
    args = {
        "command": None,
        "label": None,
        "email": None,
        "recipient": None,
        "message": None,
        "file_id": None,
        "output_path": None,
        "file_path": None,
        "dir_path": None,
        "open_mode": False,
        "ls_label": None,
        "ls_registry": False,
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
            raise SystemExit(
                "Use: slack ac <label> <email> | slack cfg | slack dm <contact_label|email> <message> [file_path] [dir_path]"
            )

        args["command"] = token
        remaining = argv[index + 1 :]
        if token == "ac":
            if len(remaining) != 2:
                raise SystemExit("Use: slack ac <label> <email>")
            args["label"], args["email"] = remaining
            return args
        if token == "cfg":
            if remaining:
                raise SystemExit("Use: slack cfg")
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
            if not remaining:
                raise SystemExit("Use: slack ls rc | slack ls [label] [-ur|-r] [-o] <number>")
            if remaining == ["rc"]:
                args["ls_registry"] = True
                return args
            parts = list(remaining)
            if "-o" in parts:
                parts.remove("-o")
                args["open_mode"] = True
            filters = [item for item in parts if item in ("-ur", "-r")]
            if len(filters) > 1:
                raise SystemExit("Use: slack ls rc | slack ls [label] [-ur|-r] [-o] <number>")
            if filters:
                filter_token = filters[0]
                args["ls_filter"] = "unread" if filter_token == "-ur" else "read"
                parts.remove(filter_token)
            if len(parts) == 1:
                limit_token = parts[0]
            elif len(parts) == 2:
                args["ls_label"] = parts[0]
                limit_token = parts[1]
            else:
                raise SystemExit("Use: slack ls rc | slack ls [label] [-ur|-r] [-o] <number>")
            try:
                args["ls_limit"] = int(limit_token)
            except ValueError:
                raise SystemExit("Use: slack ls rc | slack ls [label] [-ur|-r] [-o] <number>")
            if args["ls_limit"] <= 0:
                raise SystemExit("Number must be greater than 0.")
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
            "Use: slack ac <label> <email> | slack cfg | slack dm <contact_label|email> <message> [file_path] [dir_path] | slack df <dm_id> <file_id> [output_path] | slack o <dm_id> | slack ls rc | slack ls [label] [-ur|-r] [-o] <number> | slack sc | slack mra"
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
        with urlopen(INSTALL_URL, timeout=30) as response:
            script_body = response.read()
    except (URLError, HTTPError, TimeoutError) as exc:
        print(f"Unable to download installer: {exc}", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(delete=False, suffix="-slack-install.sh") as handle:
        handle.write(script_body)
        script_path = handle.name

    try:
        os.chmod(script_path, 0o700)
        result = subprocess.run(
            ["bash", script_path, "-u"],
            check=False,
        )
        return result.returncode
    except FileNotFoundError:
        print("Upgrade requires bash", file=sys.stderr)
        return 1
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


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


def open_config_in_editor(config_path):
    directory = os.path.dirname(config_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write("{}\n")

    editor_cmd = resolve_editor_cmd()
    try:
        subprocess.run(editor_cmd + [config_path], check=False)
    except FileNotFoundError:
        raise SystemExit(f"Editor not found: {editor_cmd[0]}")
    print(f"Opened config: {config_path}")


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
    requests = _requests()
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
        profile = user.get("profile") or {}
        email = profile.get("email") or target
        info = slack_request(
            "conversations.info",
            {"channel": channel_id, "include_num_members": "false"},
            token,
            http_method="GET",
        ).get("channel") or {}
        infos.append(
            {
                "label": label,
                "email": email,
                "user_id": user_id,
                "channel_id": channel_id,
                "info": info,
                "user": user,
            }
        )
    return infos


def get_dm_info(channel_id, token):
    info = slack_request(
        "conversations.info",
        {"channel": channel_id, "include_num_members": "false"},
        token,
        http_method="GET",
    ).get("channel") or {}
    user_id = info.get("user")
    if not user_id:
        raise SystemExit(f"Unable to resolve DM user for {channel_id}.")
    user = get_user_info(user_id, token)
    profile = user.get("profile") or {}
    return {
        "label": "-",
        "email": profile.get("email") or "-",
        "user_id": user_id,
        "channel_id": channel_id,
        "info": info,
        "user": user,
    }


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
        if file_payload.get("mode") == "snippet":
            code_blocks.append(
                {
                    "id": file_payload.get("id") or "-",
                    "name": file_payload.get("name") or "snippet",
                    "text": _snippet_text(file_payload, token),
                }
            )
            continue

        download_url = file_payload.get("url_private_download")
        if not download_url:
            continue
        destination = _download_destination(dm_id, file_payload)
        _download_file_to_path(download_url, destination, token)
        downloads.append(
            {
                "id": file_payload.get("id") or "-",
                "name": file_payload.get("name") or "attachment",
                "path": destination,
            }
        )
    return downloads, code_blocks


def _print_open_entries(entries, token):
    for index, entry in enumerate(entries, start=1):
        prefix = f"[{index}]"
        print(prefix + ("-" * max(1, 79 - len(prefix))))
        print(f"{'email':<8}: {entry['email']}")
        print(f"{'dm_id':<8}: {entry['dm_id']}")
        print(f"{'date':<8}: {format_ts(entry['message'].get('ts'))}")
        text = message_text(entry["message"]).rstrip()
        print(style_help(f"{'text':<8}: {text if text else '-'}"))

        downloads, code_blocks = _message_details(entry["message"], entry["dm_id"], token)
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


def _collect_messages(contact_dm, token, limit, filter_mode, self_user_id):
    entries = []
    info_channel = contact_dm["info"]
    last_read = info_channel.get("last_read") or "0"
    try:
        last_read_value = float(last_read)
    except (TypeError, ValueError):
        last_read_value = 0.0

    cursor = None
    matched = 0
    while True:
        history = slack_request(
            "conversations.history",
            {
                "channel": contact_dm["channel_id"],
                "limit": str(max(20, limit * 3)),
                **({"cursor": cursor} if cursor else {}),
            },
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
            if message.get("user") == self_user_id:
                continue
            is_unread = ts_value > last_read_value
            if filter_mode == "unread" and not is_unread:
                continue
            if filter_mode == "read" and is_unread:
                continue

            entries.append(
                {
                    "sort_ts": ts_value,
                    "email": contact_dm["email"],
                    "dm_id": contact_dm["channel_id"],
                    "message": message,
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


def list_dms(contacts, token, limit, filter_mode, self_user_id, open_mode):
    entries = []
    for contact_dm in get_contact_dm_infos(contacts, token):
        entries.extend(_collect_messages(contact_dm, token, limit, filter_mode, self_user_id))

    if not entries:
        if filter_mode == "unread":
            print("No unread DMs.")
        elif filter_mode == "read":
            print("No read DMs.")
        else:
            print("No DMs.")
        return

    entries.sort(key=lambda item: item["sort_ts"], reverse=True)
    selected = entries[:limit]
    selected.sort(key=lambda item: item["sort_ts"])

    if open_mode:
        _print_open_entries(selected, token)
        return

    print_sections(
        [
            [
                ("email", item["email"]),
                ("dm_id", item["dm_id"]),
                ("date", format_ts(item["message"].get("ts"))),
            ]
            for item in selected
        ]
    )


def open_dm_messages(dm_id, token, self_user_id):
    contact_dm = get_dm_info(dm_id, token)
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
                "message": message,
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
        info_channel = contact_dm["info"]
        last_read = info_channel.get("last_read") or "0"
        try:
            last_read_value = float(last_read)
        except (TypeError, ValueError):
            last_read_value = 0.0

        cursor = None
        matched = 0
        while True:
            history = slack_request(
                "conversations.history",
                {
                    "channel": contact_dm["channel_id"],
                    "limit": str(max(20, limit * 3)),
                    **({"cursor": cursor} if cursor else {}),
                },
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
                if message.get("user") == self_user_id:
                    continue
                is_unread = ts_value > last_read_value
                if filter_mode == "unread" and not is_unread:
                    continue
                if filter_mode == "read" and is_unread:
                    continue

                rows.append(
                    {
                        "sort_ts": ts_value,
                        "row": [
                        ("email", contact_dm["email"]),
                        ("dm_id", contact_dm["channel_id"]),
                        ("date", format_ts(ts)),
                        ("text", compact_text(message.get("text"))),
                        ("files", summarize_files(message)),
                    ],
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


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

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
            or args["open_mode"]
            or args["ls_label"]
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

        if not _is_version_newer(latest, __version__):
            print(f"Already running the latest version ({__version__}).")
            sys.exit(0)

        print(f"Upgrading from {__version__} to {latest}…")
        rc = _run_upgrade()
        sys.exit(rc)

    config_path = get_config_path(args["config"])

    if args["command"] == "cfg":
        open_config_in_editor(config_path)
        return

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

    if args["command"] == "ls" and args["ls_registry"]:
        list_registered_contacts(contacts)
        return

    token = resolve_token()
    auth_data = auth_test(token)

    if args["command"] == "ls":
        self_user_id = auth_data.get("user_id")
        if not self_user_id:
            raise SystemExit("Unable to determine the current Slack user.")
        list_contacts = contacts
        if args["ls_label"]:
            if args["ls_label"] not in contacts:
                raise SystemExit(f"Unknown contact label: {args['ls_label']}")
            list_contacts = {args["ls_label"]: contacts[args["ls_label"]]}
        list_dms(
            list_contacts,
            token,
            args["ls_limit"],
            args["ls_filter"],
            self_user_id,
            args["open_mode"],
        )
        return

    if args["command"] == "o":
        self_user_id = auth_data.get("user_id")
        if not self_user_id:
            raise SystemExit("Unable to determine the current Slack user.")
        open_dm_messages(args["recipient"], token, self_user_id)
        return

    if args["command"] == "mra":
        mark_all_unread_dms_as_read(contacts, token)
        return

    if args["command"] == "df":
        download_dm_file(args["recipient"], args["file_id"], args["output_path"], token)
        return

    if args["command"] == "sc":
        clear_stale_conversations(token)
        return

    if args["command"] != "dm":
        raise SystemExit(
            "Use: slack ac <label> <email> | slack cfg | slack dm <contact_label|email> <message> [file_path] [dir_path] | slack df <dm_id> <file_id> [output_path] | slack o <dm_id> | slack ls rc | slack ls [label] [-ur|-r] [-o] <number> | slack sc | slack mra"
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
