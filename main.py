import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
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
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]+$")


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


def normalize_user_labels(payload):
    labels = payload.get("user_labels", {})
    if labels is None:
        return {}
    if not isinstance(labels, dict):
        raise SystemExit("user_labels must be a JSON object.")

    cleaned = {}
    for key, value in labels.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            cleaned[key] = value.strip()
    return cleaned


def build_parser():
    parser = argparse.ArgumentParser(
        description="Send a Slack direct message as yourself."
    )
    parser.add_argument(
        "recipient",
        nargs="?",
        help="User ID (U...), email, or label.",
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Message text to send.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json for labels.",
    )
    parser.add_argument(
        "-e",
        "--edit",
        action="store_true",
        help="Open $EDITOR to compose the message.",
    )
    parser.add_argument(
        "-au",
        "--add-user",
        nargs=2,
        metavar=("LABEL", "USER_ID_OR_EMAIL"),
        help="Save a label pointing to a Slack user ID or email.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Show version and exit.",
    )
    parser.add_argument(
        "-u",
        "--upgrade",
        action="store_true",
        help="Upgrade to the latest version.",
    )
    return parser


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
        editor = os.getenv("EDITOR", "vim").strip()
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


def slack_request(method, payload, token):
    url = f"https://slack.com/api/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    if method == "users.lookupByEmail":
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


def resolve_user_id(recipient, labels, token):
    if recipient in labels:
        return labels[recipient]
    if USER_ID_RE.match(recipient or ""):
        return recipient
    if recipient and "@" in recipient:
        data = slack_request("users.lookupByEmail", {"email": recipient}, token)
        user = data.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            raise SystemExit("No user found for that email.")
        return user_id
    raise SystemExit("Recipient must be a user ID, email, or saved label.")


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


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return

    if args.upgrade:
        if args.recipient or args.text or args.edit or args.add_user:
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

    config_path = get_config_path(args.config)
    config = load_config(config_path)
    user_labels = normalize_user_labels(config)

    if args.add_user:
        if args.recipient or args.text or args.edit:
            raise SystemExit("Use --add-user by itself.")
        label, value = args.add_user
        label = label.strip()
        value = value.strip()
        if not label:
            raise SystemExit("Label cannot be empty.")
        if not value:
            raise SystemExit("User ID or email cannot be empty.")
        if USER_ID_RE.match(value):
            user_id = value
        elif "@" in value:
            token = resolve_token()
            auth_test(token)
            user_id = resolve_user_id(value, {}, token)
        else:
            raise SystemExit("Value must be a user ID or email.")
        user_labels[label] = user_id
        config["user_labels"] = user_labels
        save_config(config_path, config)
        print(f"Saved label '{label}' in {config_path}")
        return

    if args.edit and args.text:
        raise SystemExit("Use either -e or provide text, not both.")

    if args.edit:
        text = read_from_editor()
    else:
        text = " ".join(args.text).strip()

    if not args.recipient:
        parser.print_help()
        return

    if not text:
        parser.print_help()
        return

    token = resolve_token()
    auth_test(token)

    user_id = resolve_user_id(args.recipient, user_labels, token)
    channel_id, ts = send_dm(token, user_id, text)
    if ts:
        print(f"DM sent. user={user_id} channel={channel_id} ts={ts}")
    else:
        print(f"DM sent. user={user_id} channel={channel_id}")


if __name__ == "__main__":
    main()
