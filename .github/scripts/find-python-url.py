#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time

API_URL = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
TOKEN = os.environ.get("GITHUB_TOKEN")


def fetch_releases(max_attempts: int = 8) -> str:
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        cmd = [
            "curl",
            "-fsSL",
            "-L",
            "-w",
            "%{http_code}",
            "--connect-timeout",
            "10",
            "--max-time",
            "60",
        ]
        if TOKEN:
            cmd += ["-H", f"Authorization: Bearer {TOKEN}"]
        cmd.append(API_URL)

        proc = subprocess.run(cmd, capture_output=True, text=True)
        stdout = proc.stdout[:-3] if len(proc.stdout) >= 3 else ""
        status = proc.stdout[-3:]

        if proc.returncode == 0 and status == "200":
            return stdout

        if status.isdigit() and status.startswith("5") and attempt < max_attempts:
            sleep_for = min(5 * attempt, 30)
            sys.stderr.write(
                f"curl returned {status}. Retrying in {sleep_for}s (attempt {attempt}/{max_attempts})\n"
            )
            sys.stderr.flush()
            time.sleep(sleep_for)
            continue

        sys.stderr.write(proc.stderr or f"curl failed with status {status}\n")
        sys.exit(proc.returncode or 1)

    sys.stderr.write("Exceeded retry attempts fetching latest release\n")
    sys.exit(1)


result = fetch_releases()

try:
    release = json.loads(result)
except json.JSONDecodeError as exc:
    sys.stderr.write(f"Failed to parse release JSON: {exc}\n")
    sys.exit(1)

print(f"Release tag: {release.get('tag_name', 'Unknown')}", file=sys.stderr)
print(f"Number of assets: {len(release.get('assets', []))}", file=sys.stderr)

pattern = re.compile(
    r"^cpython-3\.11\.\d+\+\d{8}-x86_64-unknown-linux-gnu-.*install_only.*\.(tar\.gz|tar\.zst)$"
)

fallback_url = None

for asset in release.get("assets", []):
    name = asset.get("name", "")
    url = asset.get("browser_download_url", "")

    if "cpython-3.11" in name and "x86_64-unknown-linux-gnu" in name:
        print(f"Found relevant asset: {name}", file=sys.stderr)

    if pattern.match(name):
        if name.endswith(".tar.zst"):
            print(url)
            sys.exit(0)
        fallback_url = fallback_url or url

if fallback_url:
    print(fallback_url)
    sys.exit(0)

sys.stderr.write(
    "No matching python-build-standalone asset found for CPython 3.11 x86_64 linux-gnu in latest release\n"
)
sys.exit(1)
