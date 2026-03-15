import os
import subprocess
import tempfile
from pathlib import Path
import unittest


INSTALLER = Path(__file__).resolve().parent / "install.sh"


class InstallContractTests(unittest.TestCase):
    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def test_dash_v_without_argument_prints_latest_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            home_dir = tmp_path / "home"
            bin_dir.mkdir()
            home_dir.mkdir()

            self._write_executable(
                bin_dir / "curl",
                "#!/usr/bin/bash\n"
                "if [[ \"$*\" == *\"releases/latest\"* ]]; then\n"
                "  printf 'https://github.com/ryangerardwilson/slack/releases/tag/v0.1.21\\n'\n"
                "  exit 0\n"
                "fi\n"
                "echo unexpected curl call >&2\n"
                "exit 1\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HOME"] = str(home_dir)

            result = subprocess.run(
                ["/usr/bin/bash", str(INSTALLER), "-v"],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

            self.assertEqual(result.stdout.strip(), "0.1.21")

    def test_upgrade_same_version_uses_dash_v(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            home_dir = tmp_path / "home"
            bin_dir.mkdir()
            home_dir.mkdir()

            self._write_executable(
                bin_dir / "curl",
                "#!/usr/bin/bash\n"
                "if [[ \"$*\" == *\"releases/latest\"* ]]; then\n"
                "  printf 'https://github.com/ryangerardwilson/slack/releases/tag/v0.1.21\\n'\n"
                "  exit 0\n"
                "fi\n"
                "echo unexpected curl call >&2\n"
                "exit 1\n",
            )
            self._write_executable(
                bin_dir / "slack",
                "#!/usr/bin/bash\n"
                "if [[ \"$1\" == \"-v\" ]]; then\n"
                "  printf '0.1.21\\n'\n"
                "  exit 0\n"
                "fi\n"
                "echo unexpected invocation >&2\n"
                "exit 1\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HOME"] = str(home_dir)

            result = subprocess.run(
                ["/usr/bin/bash", str(INSTALLER), "-u"],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

            self.assertIn("already installed", result.stdout)


if __name__ == "__main__":
    unittest.main()
