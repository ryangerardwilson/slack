import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1]
MAIN_PATH = APP_DIR / "main.py"
VERSION_PATH = APP_DIR / "_version.py"
CONTRACT_SRC = APP_DIR.parent / "rgw_cli_contract" / "src"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(CONTRACT_SRC))


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
        env = os.environ.copy()
        existing = env.get("PYTHONPATH")
        parts = [str(CONTRACT_SRC)]
        if existing:
            parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(parts)
        return subprocess.run(
            [sys.executable, str(MAIN_PATH), *args],
            cwd=APP_DIR,
            check=False,
            capture_output=True,
            text=True,
            env=env,
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

    def test_main_delegates_upgrade_to_contract_runtime(self):
        module = load_main_module()
        with mock.patch.object(module, "run_app", return_value=0) as run_app:
            rc = module.main(["-u"])
        self.assertEqual(rc, 0)
        run_app.assert_called_once()
        self.assertEqual(run_app.call_args.args[0], module.APP_SPEC)
        self.assertEqual(run_app.call_args.args[1], ["-u"])
        self.assertIs(run_app.call_args.args[2], module._dispatch)

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
            self.assertEqual(stdout.getvalue(), "")

    def test_dm_accepts_multiple_attachment_paths(self):
        module = load_main_module()

        parsed = module.parse_args(
            [
                "dm",
                "ar",
                "hello",
                "/tmp/file1.csv",
                "/tmp/folder",
                "/tmp/file2.csv",
            ]
        )

        self.assertEqual(parsed["command"], "dm")
        self.assertEqual(parsed["recipient"], "ar")
        self.assertEqual(parsed["message"], "hello")
        self.assertEqual(
            parsed["paths"],
            ["/tmp/file1.csv", "/tmp/folder", "/tmp/file2.csv"],
        )

    def test_send_attachments_uploads_all_files_and_directories(self):
        module = load_main_module()
        recorded = []

        def fake_upload(channel_id, thread_ts, path, filename, token):
            recorded.append((channel_id, thread_ts, path, filename, token))
            return f"id-{filename}"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            file_one = temp_path / "one.csv"
            file_two = temp_path / "two.csv"
            folder = temp_path / "export"
            nested = folder / "nested.txt"
            file_one.write_text("a\n", encoding="utf-8")
            file_two.write_text("b\n", encoding="utf-8")
            folder.mkdir()
            nested.write_text("c\n", encoding="utf-8")

            with mock.patch.object(module, "_upload_external_file", side_effect=fake_upload):
                uploaded = module.send_attachments(
                    "C123",
                    "123.456",
                    [str(file_one), str(folder), str(file_two)],
                    "token",
                )

        self.assertEqual(uploaded[0], "one.csv")
        self.assertEqual(uploaded[1], "export.zip")
        self.assertEqual(uploaded[2], "two.csv")
        self.assertEqual(len(recorded), 3)
        self.assertEqual(recorded[0][3], "one.csv")
        self.assertEqual(recorded[1][3], "export.zip")
        self.assertEqual(recorded[2][3], "two.csv")


if __name__ == "__main__":
    unittest.main()
