import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1]
MAIN_PATH = APP_DIR / "main.py"
VERSION_PATH = APP_DIR / "_version.py"


def load_main_module():
    spec = importlib.util.spec_from_file_location("slack_main_test", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_version():
    namespace = {}
    exec(VERSION_PATH.read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


class CliContractTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MAIN_PATH), *args],
            cwd=APP_DIR,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_no_args_matches_help(self):
        bare = self.run_cli()
        help_run = self.run_cli("-h")

        self.assertEqual(bare.returncode, 0)
        self.assertEqual(help_run.returncode, 0)
        self.assertEqual(bare.stdout, help_run.stdout)
        self.assertEqual(bare.stderr, help_run.stderr)
        self.assertIn("features:", bare.stdout)

    def test_version_comes_from_single_release_source(self):
        result = self.run_cli("-v")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, load_version() + "\n")
        self.assertEqual(result.stderr, "")

    def test_main_does_not_define_a_fallback_version_string(self):
        source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertNotIn('__version__ = "0.0.0"', source)

    def test_upgrade_downloads_installer_and_calls_it_with_dash_u(self):
        module = load_main_module()
        recorded = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"#!/usr/bin/env bash\nexit 0\n"

        def fake_run(cmd, check):
            recorded["cmd"] = cmd
            recorded["check"] = check

            class Result:
                returncode = 0

            return Result()

        with mock.patch.object(module, "urlopen", return_value=FakeResponse()):
            with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                rc = module._run_upgrade()

        self.assertEqual(rc, 0)
        self.assertEqual(recorded["cmd"][0], "bash")
        self.assertEqual(recorded["cmd"][2], "-u")
        self.assertFalse(recorded["check"])

    def test_cfg_opens_real_config_file_with_editor_resolution_order(self):
        module = load_main_module()
        recorded = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "cfg-home"
            config_path = config_home / "slack" / "config.json"

            def fake_run(cmd, check):
                recorded["cmd"] = cmd
                recorded["check"] = check

                class Result:
                    returncode = 0

                return Result()

            with mock.patch.dict(
                module.os.environ,
                {"XDG_CONFIG_HOME": str(config_home), "VISUAL": "nano", "EDITOR": "vi"},
                clear=False,
            ):
                with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                    with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                        module.main(["cfg"])

            self.assertTrue(config_path.exists())
            self.assertEqual(config_path.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(recorded["cmd"], ["nano", str(config_path)])
            self.assertFalse(recorded["check"])
            self.assertIn(str(config_path), stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
