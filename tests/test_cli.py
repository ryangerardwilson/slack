import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
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
            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                '{\n  "accounts": {}\n}\n',
            )
            self.assertEqual(recorded["cmd"], ["nano", str(config_path)])
            self.assertFalse(recorded["check"])
            self.assertEqual(stdout.getvalue(), "")

    def test_post_accepts_multiple_attachment_paths(self):
        module = load_main_module()

        parsed = module.parse_args(
            [
                "post",
                "ar",
                "hello",
                "/tmp/file1.csv",
                "/tmp/folder",
                "/tmp/file2.csv",
            ]
        )

        self.assertEqual(parsed["command"], "post")
        self.assertEqual(parsed["recipient"], "ar")
        self.assertEqual(parsed["message"], "hello")
        self.assertEqual(
            parsed["paths"],
            ["/tmp/file1.csv", "/tmp/folder", "/tmp/file2.csv"],
        )

    def test_preset_prefix_parses_command(self):
        module = load_main_module()

        parsed = module.parse_args(["2", "post", "C123", "hello"])

        self.assertEqual(parsed["preset"], "2")
        self.assertEqual(parsed["command"], "post")
        self.assertEqual(parsed["recipient"], "C123")
        self.assertEqual(parsed["message"], "hello")

    def test_dm_alias_parses_as_post(self):
        module = load_main_module()

        parsed = module.parse_args(["dm", "ar", "hello"])

        self.assertEqual(parsed["command"], "post")
        self.assertEqual(parsed["recipient"], "ar")
        self.assertEqual(parsed["message"], "hello")

    def test_auth_parses_token_storage_flags(self):
        module = load_main_module()

        parsed = module.parse_args(
            ["auth", "2", "-bt", "xoxb-bot", "-ut", "xoxp-user", "-at", "xapp-app", "-n", "work"]
        )

        self.assertEqual(parsed["command"], "auth")
        self.assertEqual(parsed["auth_preset"], "2")
        self.assertEqual(parsed["auth_bot_token"], "xoxb-bot")
        self.assertEqual(parsed["auth_user_token"], "xoxp-user")
        self.assertEqual(parsed["auth_app_token"], "xapp-app")
        self.assertEqual(parsed["auth_name"], "work")

        prefixed = module.parse_args(["2", "auth", "-i"])
        self.assertEqual(prefixed["command"], "auth")
        self.assertEqual(prefixed["auth_preset"], "2")
        self.assertTrue(prefixed["auth_import"])

    def test_codex_command_parses_event_service_actions(self):
        module = load_main_module()

        parsed = module.parse_args(["1", "codex", "service"])

        self.assertEqual(parsed["preset"], "1")
        self.assertEqual(parsed["command"], "codex")
        self.assertEqual(parsed["codex_action"], "service")

        logs = module.parse_args(["1", "codex", "logs", "120"])
        self.assertEqual(logs["codex_action"], "logs")
        self.assertEqual(logs["codex_lines"], 120)
        scan = module.parse_args(["1", "codex", "scan"])
        self.assertEqual(scan["codex_action"], "scan")

    def test_select_account_requires_preset_when_accounts_exist(self):
        module = load_main_module()

        with self.assertRaises(SystemExit):
            module.select_account(
                {
                    "accounts": {
                        "1": {"bot_token": "xoxb-one"},
                        "2": {"bot_token": "xoxb-two"},
                    },
                }
            )

        preset, account = module.select_account(
            {"accounts": {"2": {"bot_token": "xoxb-two"}}},
            "2",
        )

        self.assertEqual(preset, "2")
        self.assertEqual(account["bot_token"], "xoxb-two")

    def test_contacts_are_unique_to_account_presets(self):
        module = load_main_module()

        contacts = module.contacts_for_account(
            {"contacts": {"root": "root@example.com"}},
            {"contacts": {"acct": "acct@example.com", "root": "override@example.com"}},
        )

        self.assertEqual(
            contacts,
            {"acct": "acct@example.com", "root": "override@example.com"},
        )

    def test_resolve_token_reads_direct_config_tokens(self):
        module = load_main_module()

        self.assertEqual(module.resolve_token({"bot_token": "xoxb-config"}), "xoxb-config")
        self.assertEqual(module.resolve_list_token({"user_token": "xoxp-config"}), "xoxp-config")
        self.assertEqual(module.resolve_app_token({"app_token": "xapp-config"}), "xapp-config")

    def test_reply_requires_message_id_target(self):
        module = load_main_module()

        parsed = module.parse_args(["reply", "C123:100.000100", "hello"])

        self.assertEqual(parsed["command"], "reply")
        self.assertEqual(parsed["recipient"], "C123:100.000100")
        self.assertEqual(parsed["message"], "hello")
        with self.assertRaises(SystemExit):
            module.parse_args(["reply", "C123", "hello"])

    def test_resolve_post_target_accepts_channel_and_message_ids(self):
        module = load_main_module()

        channel_target = module.resolve_post_target("C123", {}, "token")
        message_target = module.resolve_post_target("C123:100.000100", {}, "token")

        self.assertEqual(channel_target["kind"], "conversation")
        self.assertEqual(channel_target["channel_id"], "C123")
        self.assertEqual(message_target["kind"], "message")
        self.assertEqual(message_target["channel_id"], "C123")
        self.assertEqual(message_target["message_ts"], "100.000100")

    def test_resolve_post_target_accepts_contact_labels(self):
        module = load_main_module()

        with mock.patch.object(module, "lookup_user_id_by_email", return_value="U123"):
            with mock.patch.object(module, "open_dm", return_value="D123"):
                target = module.resolve_post_target(
                    "ar",
                    {"ar": "ashish.raj@example.com"},
                    "token",
                )

        self.assertEqual(target["kind"], "email")
        self.assertEqual(target["label"], "ar")
        self.assertEqual(target["target"], "ar")
        self.assertEqual(target["channel_id"], "D123")

    def test_post_dispatch_sends_to_channel_id(self):
        module = load_main_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "cfg-home"
            config_path = config_home / "slack" / "config.json"
            token_path = Path(temp_dir) / "bot-token"
            config_path.parent.mkdir(parents=True)
            token_path.write_text("xoxb-token\n", encoding="utf-8")
            config_path.write_text(
                '{"bot_token_file": "' + str(token_path) + '"}\n',
                encoding="utf-8",
            )

            with mock.patch.dict(
                module.os.environ,
                {"XDG_CONFIG_HOME": str(config_home)},
                clear=True,
            ):
                with mock.patch.object(module, "auth_test", return_value={"ok": True}):
                    with mock.patch.object(module, "send_post", return_value="200.000100") as send_post:
                        with mock.patch.object(
                            module,
                            "send_attachments",
                            return_value=["report.csv"],
                        ) as send_attachments:
                            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                                rc = module.main(["post", "C123", "hello", "/tmp/report.csv"])

        self.assertEqual(rc, 0)
        send_post.assert_called_once_with("xoxb-token", "C123", "hello")
        send_attachments.assert_called_once_with(
            "C123",
            "200.000100",
            ["/tmp/report.csv"],
            "xoxb-token",
        )
        self.assertIn(
            "posted target=C123 kind=conversation channel=C123 ts=200.000100 files=report.csv",
            stdout.getvalue(),
        )

    def test_auth_stores_tokens_inside_config_accounts(self):
        module = load_main_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "cfg-home"
            config_path = config_home / "slack" / "config.json"

            with mock.patch.dict(
                module.os.environ,
                {"XDG_CONFIG_HOME": str(config_home)},
                clear=True,
            ):
                with mock.patch.object(
                    module,
                    "auth_test",
                    return_value={"ok": True, "team": "Work", "team_id": "T123", "user_id": "U123"},
                ):
                    with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                        rc = module.main(
                            [
                                "auth",
                                "1",
                                "-bt",
                                "xoxb-token",
                                "-ut",
                                "xoxp-token",
                                "-at",
                                "xapp-token",
                                "-n",
                                "work",
                            ]
                        )
                        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        account = config["accounts"]["1"]
        self.assertEqual(account["bot_token"], "xoxb-token")
        self.assertEqual(account["user_token"], "xoxp-token")
        self.assertEqual(account["app_token"], "xapp-token")
        self.assertEqual(account["name"], "work")
        self.assertNotIn("defaults", config)
        self.assertIn("authorized preset=1", stdout.getvalue())
        self.assertNotIn("xoxb-token", stdout.getvalue())
        self.assertNotIn("xoxp-token", stdout.getvalue())
        self.assertNotIn("xapp-token", stdout.getvalue())

    def test_auth_import_reads_legacy_token_files(self):
        module = load_main_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "cfg-home"
            bot_path = Path(temp_dir) / "bot-token"
            user_path = Path(temp_dir) / "user-token"
            bot_path.write_text("xoxb-token\n", encoding="utf-8")
            user_path.write_text("xoxp-token\n", encoding="utf-8")
            app_path = Path(temp_dir) / "app-token"
            app_path.write_text("xapp-token\n", encoding="utf-8")

            with mock.patch.dict(
                module.os.environ,
                {"XDG_CONFIG_HOME": str(config_home)},
                clear=True,
            ):
                    with mock.patch.object(module, "DEFAULT_BOT_TOKEN_FILE", str(bot_path)):
                        with mock.patch.object(module, "DEFAULT_USER_TOKEN_FILE", str(user_path)):
                            with mock.patch.object(module, "DEFAULT_APP_TOKEN_FILE", str(app_path)):
                                with mock.patch.object(module, "auth_test", return_value={"ok": True}):
                                    rc = module.main(["auth", "1", "-i"])

            config_path = config_home / "slack" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(config["accounts"]["1"]["bot_token"], "xoxb-token")
        self.assertEqual(config["accounts"]["1"]["user_token"], "xoxp-token")
        self.assertEqual(config["accounts"]["1"]["app_token"], "xapp-token")

    def test_reply_dispatch_sends_thread_reply(self):
        module = load_main_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "cfg-home"
            config_path = config_home / "slack" / "config.json"
            token_path = Path(temp_dir) / "bot-token"
            config_path.parent.mkdir(parents=True)
            token_path.write_text("xoxb-token\n", encoding="utf-8")
            config_path.write_text(
                '{"bot_token_file": "' + str(token_path) + '"}\n',
                encoding="utf-8",
            )

            with mock.patch.dict(
                module.os.environ,
                {"XDG_CONFIG_HOME": str(config_home)},
                clear=True,
            ):
                with mock.patch.object(module, "auth_test", return_value={"ok": True}):
                    with mock.patch.object(
                        module,
                        "resolve_reply_thread_ts",
                        return_value="100.000100",
                    ):
                        with mock.patch.object(module, "send_post", return_value="200.000100") as send_post:
                            with mock.patch.object(
                                module,
                                "send_attachments",
                                return_value=[],
                            ) as send_attachments:
                                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                                    rc = module.main(
                                        ["reply", "C123:100.000100", "hello"]
                                    )

        self.assertEqual(rc, 0)
        send_post.assert_called_once_with(
            "xoxb-token",
            "C123",
            "hello",
            thread_ts="100.000100",
        )
        send_attachments.assert_called_once_with("C123", "100.000100", [], "xoxb-token")
        self.assertIn(
            "replied message_id=C123:100.000100 channel=C123 thread_ts=100.000100 ts=200.000100",
            stdout.getvalue(),
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

    def test_su_accepts_query(self):
        module = load_main_module()

        parsed = module.parse_args(["su", "rohan", "choudhary"])

        self.assertEqual(parsed["command"], "su")
        self.assertEqual(parsed["query"], "rohan choudhary")

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
                "_conversation_summary",
                return_value={
                    "email": "sender@example.com",
                    "info": {"last_read": "50.0"},
                    "channel_id": "D123",
                    "surface": "dm",
                    "conversation": "Sender <sender@example.com>",
                    "members": "2",
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
        self.assertEqual(entries[0]["channel_id"], "D123")
        self.assertEqual(entries[0]["surface"], "dm")
        self.assertEqual(entries[0]["conversation"], "Sender <sender@example.com>")
        self.assertEqual(entries[0]["sender"]["name"], "Sender")

    def test_search_dms_labels_channel_surfaces(self):
        module = load_main_module()

        def fake_slack_request(method, payload, token, **kwargs):
            self.assertEqual(token, "xoxp-token")
            if method == "search.messages":
                return {
                    "ok": True,
                    "messages": {
                        "matches": [
                            {
                                "channel": {"id": "C123", "name": "growth"},
                                "ts": "100.000100",
                                "user": "U222",
                                "text": "hello team",
                            }
                        ]
                    },
                }
            raise AssertionError(method)

        with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
            with mock.patch.object(
                module,
                "_conversation_summary",
                side_effect=SystemExit("missing_scope"),
            ):
                with mock.patch.object(
                    module,
                    "_hydrate_message",
                    return_value={"ts": "100.000100", "user": "U222", "text": "hello team"},
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
        self.assertEqual(entries[0]["channel_id"], "C123")
        self.assertEqual(entries[0]["surface"], "channel")
        self.assertEqual(entries[0]["conversation"], "#growth")

    def test_fallback_conversation_summary_formats_group_dm_names(self):
        module = load_main_module()

        summary = module._fallback_conversation_summary(
            "C123",
            {
                "id": "C123",
                "name": "mpdm-rohan.choudhary--ryan.wilson--maanas.dwivedi064-1",
            },
        )

        self.assertEqual(summary["surface"], "group_dm")
        self.assertEqual(
            summary["conversation"],
            "rohan.choudhary, ryan.wilson, maanas.dwivedi064",
        )

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

    def test_search_users_and_contacts_prints_contact_and_user_matches(self):
        module = load_main_module()

        def fake_slack_request(method, payload, token, **kwargs):
            self.assertEqual(method, "users.list")
            return {
                "ok": True,
                "members": [
                    {
                        "id": "U2",
                        "name": "rohan.choudhary",
                        "profile": {
                            "real_name": "Rohan Choudhary",
                            "email": "rohan.choudhary@example.com",
                        },
                    }
                ],
                "response_metadata": {"next_cursor": ""},
            }

        contacts = {"rohan": "rohan.choudhary@example.com"}
        with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                module.search_users_and_contacts(contacts, "token", "rohan", limit=5)

        output = stdout.getvalue()
        self.assertIn("source  : contact", output)
        self.assertIn("source  : user", output)
        self.assertIn("label   : rohan", output)
        self.assertIn("user_id : U2", output)

    def test_open_message_id_downloads_all_message_attachments(self):
        module = load_main_module()

        message = {
            "ts": "100.000100",
            "user": "U2",
            "text": "see files",
            "files": [
                {
                    "id": "F1",
                    "name": "note.txt",
                    "url_private_download": "https://files.example/note",
                },
                {
                    "id": "F2",
                    "name": "snippet.py",
                    "mode": "snippet",
                    "url_private_download": "https://files.example/snippet",
                },
            ],
            "attachments": [
                {
                    "title": "Embedded plan",
                    "title_link": "https://docs.example/plan",
                    "text": "doc preview",
                }
            ],
        }

        def fake_slack_request(method, payload, token, **kwargs):
            if method == "conversations.info":
                return {"ok": True, "channel": {"user": "U2", "last_read": "0"}}
            if method == "conversations.history":
                return {"ok": True, "messages": [message]}
            if method == "conversations.mark":
                return {"ok": True}
            raise AssertionError(method)

        def fake_download_bytes(url, token):
            if url.endswith("/snippet"):
                return b"print('hi')\n"
            return b"hello\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            def fake_zip_destination(channel_id, ts):
                return str(temp_path / f"{channel_id}-{ts}-attachments.zip")

            with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
                with mock.patch.object(
                    module,
                    "get_user_info",
                    return_value={
                        "id": "U2",
                        "name": "rohan.choudhary",
                        "profile": {
                            "real_name": "Rohan Choudhary",
                            "email": "rohan.choudhary@example.com",
                        },
                    },
                ):
                    with mock.patch.object(module, "_download_url_bytes", side_effect=fake_download_bytes):
                        with mock.patch.object(module, "_message_zip_destination", side_effect=fake_zip_destination):
                            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                                module.open_dm_messages("D123:100.000100", "token", "U1")

            zip_path = temp_path / "D123-100.000100-attachments.zip"
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.read("note.txt"), b"hello\n")
                self.assertEqual(archive.read("snippet.py"), b"print('hi')\n")
                self.assertIn("url: https://docs.example/plan", archive.read("Embedded plan.url.txt").decode())
            output = stdout.getvalue()
            self.assertIn("message_id: D123:100.000100", output)
            self.assertIn("zip     : ", output)
            self.assertIn("file    : F1 note.txt note.txt", output)
            self.assertIn("file    : F2 snippet.py snippet.py", output)
            self.assertIn("embed   : - Embedded plan Embedded plan.url.txt", output)
            self.assertIn("code    : F2 snippet.py", output)

    def test_summarize_attachments_shows_names_only(self):
        module = load_main_module()

        message = {
            "files": [{"id": "F1", "name": "forecast.csv"}],
            "attachments": [
                {
                    "title": "Embedded doc",
                    "title_link": "https://docs.example/doc",
                }
            ],
        }

        self.assertEqual(module.summarize_attachments(message), "forecast.csv, Embedded doc")

    def test_open_message_id_falls_back_without_conversation_info(self):
        module = load_main_module()

        message = {"ts": "100.000100", "user": "U2", "text": "group update"}

        def fake_slack_request(method, payload, token, **kwargs):
            if method == "conversations.info":
                raise SystemExit("missing_scope")
            if method == "conversations.history":
                return {"ok": True, "messages": [message]}
            if method == "conversations.mark":
                return {"ok": True}
            raise AssertionError(method)

        with mock.patch.object(module, "slack_request", side_effect=fake_slack_request):
            with mock.patch.object(
                module,
                "get_user_info",
                return_value={
                    "id": "U2",
                    "name": "rohan.choudhary",
                    "profile": {
                        "real_name": "Rohan Choudhary",
                        "email": "rohan.choudhary@example.com",
                    },
                },
            ):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    module.open_dm_messages("C123:100.000100", "token", "U1")

        output = stdout.getvalue()
        self.assertIn("message_id: C123:100.000100", output)
        self.assertIn("surface     : channel", output)
        self.assertIn("channel_id  : C123", output)

    def test_slack_event_filter_accepts_dm_and_mentions_only(self):
        module = load_main_module()

        dm = module._eligible_slack_event(
            {
                "type": "message",
                "channel": "D123",
                "channel_type": "im",
                "user": "U222",
                "text": "hello",
                "ts": "100.000100",
            },
            "UAPP",
        )
        mention = module._eligible_slack_event(
            {
                "type": "app_mention",
                "channel": "C123",
                "user": "U222",
                "text": "<@UAPP> hello",
                "ts": "100.000100",
            },
            "UAPP",
        )
        channel_message = module._eligible_slack_event(
            {
                "type": "message",
                "channel": "C123",
                "channel_type": "channel",
                "user": "U222",
                "text": "hello channel",
                "ts": "100.000100",
            },
            "UAPP",
        )

        self.assertEqual(dm["kind"], "direct_message")
        self.assertEqual(mention["kind"], "app_mention")
        self.assertEqual(mention["text"], "hello")
        self.assertIsNone(channel_message)

    def test_codex_resume_for_slack_calls_current_session(self):
        module = load_main_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            state_file = Path(temp_dir) / "state.json"
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured["cwd"] = kwargs["cwd"]
                captured["input"] = kwargs["input"]
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text("slack reply\n", encoding="utf-8")

                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return Result()

            account = {
                "codex_session_id": "session-1",
                "codex_workspace": str(workspace),
                "codex_state_file": str(state_file),
                "codex_args": ["--skip-git-repo-check", "--full-auto"],
                "codex_timeout_seconds": 1,
            }
            event = {
                "kind": "direct_message",
                "channel_id": "D123",
                "user_id": "U222",
                "text": "hello codex",
                "ts": "100.000100",
            }

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                reply = module.codex_resume_for_slack(account, event)

        self.assertEqual(reply, "slack reply")
        self.assertEqual(captured["command"][:4], ["codex", "exec", "resume", "session-1"])
        self.assertIn("--full-auto", captured["command"])
        self.assertEqual(captured["cwd"], workspace)
        self.assertIn("hello codex", captured["input"])

    def test_user_dm_entries_use_user_token_for_reply(self):
        module = load_main_module()

        entry = {
            "sort_ts": 100.000100,
            "surface": "dm",
            "channel_id": "D123",
            "dm_id": "D123",
            "sender": {"id": "U222"},
            "message": {"ts": "100.000100", "user": "U222", "text": "hello"},
        }
        event_info = module._event_info_from_dm_entry(entry)

        self.assertEqual(event_info["kind"], "user_direct_message")
        self.assertEqual(event_info["channel_id"], "D123")
        self.assertEqual(event_info["text"], "hello")

        account = {"bot_token": "xoxb-bot", "user_token": "xoxp-user"}
        with mock.patch.object(module, "send_post", return_value="200.000100") as send_post:
            module._send_codex_reply(account, event_info, "reply")

        send_post.assert_called_once_with("xoxp-user", "D123", "reply", thread_ts=None)

        mention = {"kind": "user_mention", "channel_id": "C123", "thread_ts": "100.000100"}
        with mock.patch.object(module, "send_post", return_value="200.000100") as send_post:
            module._send_codex_reply(account, mention, "thread reply")

        send_post.assert_called_once_with(
            "xoxp-user",
            "C123",
            "thread reply",
            thread_ts="100.000100",
        )

    def test_user_dm_entry_skips_group_dm(self):
        module = load_main_module()

        event_info = module._event_info_from_dm_entry(
            {
                "surface": "group_dm",
                "channel_id": "G123",
                "sender": {"id": "U222"},
                "message": {"ts": "100.000100", "user": "U222", "text": "hello"},
            }
        )

        self.assertIsNone(event_info)

    def test_user_mention_match_replies_in_thread(self):
        module = load_main_module()

        match = {
            "channel": {"id": "C123"},
            "ts": "100.000100",
            "user": "U222",
            "text": "hi <@U111>",
        }
        with mock.patch.object(
            module,
            "_hydrate_message",
            return_value={"ts": "100.000100", "user": "U222", "text": "hi <@U111>"},
        ):
            event_info = module._user_mention_event_from_match(match, "token", "U111")

        self.assertEqual(event_info["kind"], "user_mention")
        self.assertEqual(event_info["channel_id"], "C123")
        self.assertEqual(event_info["thread_ts"], "100.000100")


if __name__ == "__main__":
    unittest.main()
