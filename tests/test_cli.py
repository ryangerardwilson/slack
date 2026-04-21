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

    def test_resolve_token_prefers_openclaw_bot_token_file(self):
        module = load_main_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "slack-bot-token"
            token_path.write_text("xoxb-bot-token\n", encoding="utf-8")

            with mock.patch.dict(module.os.environ, {}, clear=True):
                token = module.resolve_token({"bot_token_file": str(token_path)})

        self.assertEqual(token, "xoxb-bot-token")

    def test_auth_test_accepts_bot_tokens(self):
        module = load_main_module()

        with mock.patch.object(
            module,
            "slack_request",
            return_value={"ok": True, "bot_id": "B123", "user_id": "U123"},
        ) as slack_request:
            data = module.auth_test("xoxb-token")

        self.assertEqual(data["bot_id"], "B123")
        slack_request.assert_called_once_with("auth.test", {}, "xoxb-token")

    def test_ls_accepts_gmail_style_filters(self):
        module = load_main_module()

        parsed = module.parse_args(
            ["ls", "-f", "maanas", "-c", "invoice", "-tl", "2w", "-l", "20", "-o"]
        )

        self.assertEqual(parsed["command"], "ls")
        self.assertEqual(parsed["ls_limit"], 20)
        self.assertEqual(parsed["ls_from"], "maanas")
        self.assertEqual(parsed["ls_contains"], "invoice")
        self.assertEqual(parsed["ls_time_limit"], "2w")
        self.assertTrue(parsed["open_mode"])

    def test_ls_without_label_scans_accessible_dms(self):
        module = load_main_module()
        calls = {}

        def fake_list_dms(
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
            calls.update(
                {
                    "contacts": contacts,
                    "token": token,
                    "limit": limit,
                    "filter_mode": filter_mode,
                    "self_user_id": self_user_id,
                    "open_mode": open_mode,
                    "label": label,
                    "sender_filter": sender_filter,
                    "contains_filter": contains_filter,
                    "time_limit": time_limit,
                }
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "cfg-home"
            config_path = config_home / "slack" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"contacts": {"md": "md@example.com"}}\n', encoding="utf-8")
            token_path = Path(temp_dir) / "user-token"
            token_path.write_text("xoxp-token\n", encoding="utf-8")

            with mock.patch.dict(
                module.os.environ,
                {"XDG_CONFIG_HOME": str(config_home)},
                clear=True,
            ):
                with mock.patch.object(module, "DEFAULT_USER_TOKEN_FILE", str(token_path)):
                    with mock.patch.object(
                        module,
                        "auth_test",
                        return_value={"ok": True, "user_id": "U123"},
                    ):
                        with mock.patch.object(module, "list_dms", side_effect=fake_list_dms):
                            rc = module.main(["ls", "-ur", "-f", "maanas", "-l", "5"])

        self.assertEqual(rc, 0)
        self.assertEqual(calls["contacts"], {"md": "md@example.com"})
        self.assertEqual(calls["token"], "xoxp-token")
        self.assertEqual(calls["limit"], 5)
        self.assertEqual(calls["filter_mode"], "unread")
        self.assertEqual(calls["self_user_id"], "U123")
        self.assertFalse(calls["open_mode"])
        self.assertIsNone(calls["label"])
        self.assertEqual(calls["sender_filter"], "maanas")

    def test_search_dms_uses_user_token_fast_path(self):
        module = load_main_module()

        def fake_slack_request(method, payload, token, **kwargs):
            self.assertEqual(token, "xoxp-token")
            if method == "search.messages":
                self.assertEqual(payload["query"], "is:dm")
                return {
                    "ok": True,
                    "messages": {
                        "matches": [
                            {
                                "channel": {"id": "D123"},
                                "ts": "100.000100",
                                "user": "U222",
                                "text": "hello",
                            }
                        ]
                    },
                }
            raise AssertionError(method)

        with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
            with mock.patch.object(
                module,
                "get_dm_info",
                return_value={
                    "email": "sender@example.com",
                    "info": {"last_read": "50.0"},
                    "channel_id": "D123",
                },
            ):
                with mock.patch.object(
                    module,
                    "_hydrate_message",
                    return_value={"ts": "100.000100", "user": "U222", "text": "hello"},
                ):
                    with mock.patch.object(
                        module,
                        "_sender_info",
                        return_value={
                            "id": "U222",
                            "name": "Sender",
                            "email": "sender@example.com",
                            "label": "Sender <sender@example.com>",
                        },
                    ):
                        entries = module.search_dms(
                            {},
                            "xoxp-token",
                            10,
                            "all",
                            "U111",
                            False,
                        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["dm_id"], "D123")
        self.assertEqual(entries[0]["sender"]["name"], "Sender")

    def test_lookup_user_id_by_name_resolves_unique_exact_match(self):
        module = load_main_module()

        def fake_slack_request(method, payload, token, **kwargs):
            self.assertEqual(method, "users.list")
            return {
                "ok": True,
                "members": [
                    {
                        "id": "U1",
                        "name": "rohan.agarwal",
                        "profile": {"real_name": "Rohan Agarwal"},
                    },
                    {
                        "id": "U2",
                        "name": "rohan.choudhary",
                        "profile": {"real_name": "Rohan Choudhary"},
                    },
                ],
                "response_metadata": {"next_cursor": ""},
            }

        with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
            self.assertEqual(module.lookup_user_id_by_name("Rohan Choudhary", "token"), "U2")

    def test_lookup_user_id_by_name_leaves_ambiguous_partial_unresolved(self):
        module = load_main_module()

        def fake_slack_request(method, payload, token, **kwargs):
            return {
                "ok": True,
                "members": [
                    {
                        "id": "U1",
                        "name": "rohan.agarwal",
                        "profile": {"real_name": "Rohan Agarwal"},
                    },
                    {
                        "id": "U2",
                        "name": "rohan.choudhary",
                        "profile": {"real_name": "Rohan Choudhary"},
                    },
                ],
                "response_metadata": {"next_cursor": ""},
            }

        with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
            self.assertIsNone(module.lookup_user_id_by_name("rohan", "token"))


if __name__ == "__main__":
    unittest.main()
