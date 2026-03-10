import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
INSTALL_PATH = APP_DIR / "install.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class InstallerContractTests(unittest.TestCase):
    def run_installer(self, temp_home: Path, fake_bin: Path, *args):
        env = os.environ.copy()
        env["HOME"] = str(temp_home)
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["SHELL"] = "/bin/bash"
        return subprocess.run(
            ["bash", str(INSTALL_PATH), *args],
            cwd=APP_DIR,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_dash_v_without_argument_prints_latest_release_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir) / "home"
            fake_bin = Path(temp_dir) / "bin"
            temp_home.mkdir()
            fake_bin.mkdir()

            write_executable(
                fake_bin / "curl",
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "$*" == *"releases/latest"* ]]; then
                      printf '%s\n' '{"tag_name":"v9.8.7"}'
                      exit 0
                    fi
                    echo "unexpected curl invocation: $*" >&2
                    exit 1
                    """
                ),
            )

            result = self.run_installer(temp_home, fake_bin, "-v")

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(result.stdout.strip(), "9.8.7")

    def test_same_version_check_uses_app_dash_v(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir) / "home"
            fake_bin = Path(temp_dir) / "bin"
            temp_home.mkdir()
            fake_bin.mkdir()
            log_path = Path(temp_dir) / "slack-args.log"

            write_executable(
                fake_bin / "curl",
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "$*" == *"releases/tag/v0.1.29"* ]]; then
                      printf '200'
                      exit 0
                    fi
                    echo "unexpected curl invocation: $*" >&2
                    exit 1
                    """
                ),
            )

            write_executable(
                fake_bin / "slack",
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    printf '%s\n' "$*" >> "{log_path}"
                    if [[ "$1" == "-v" ]]; then
                      printf '0.1.29\n'
                      exit 0
                    fi
                    exit 1
                    """
                ),
            )

            result = self.run_installer(temp_home, fake_bin, "-v", "0.1.29", "--no-modify-path")

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("already installed", result.stdout)
            self.assertEqual(log_path.read_text(encoding="utf-8").strip(), "-v")

    def test_upgrade_rejects_binary_combination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir) / "home"
            fake_bin = Path(temp_dir) / "bin"
            temp_home.mkdir()
            fake_bin.mkdir()

            result = self.run_installer(temp_home, fake_bin, "-u", "-b", "/tmp/slack", "--no-modify-path")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Do not combine -u with -b.", result.stdout)


if __name__ == "__main__":
    unittest.main()
