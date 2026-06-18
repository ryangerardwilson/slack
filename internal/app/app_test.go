package app

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHelpAndParseContract(t *testing.T) {
	var stdout bytes.Buffer
	rt := NewRuntime()
	rt.Stdout = &stdout
	rt.Stderr = &bytes.Buffer{}
	if err := rt.Run(nil); err != nil {
		t.Fatalf("Run help: %v", err)
	}
	for _, want := range []string{
		"slack <preset> list channels",
		"slack <preset> list dms",
		"slack accounts list",
		"slack 1 inspect message",
		"slack 1 preview send",
		"slack mark all read",
		"output json",
	} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("help missing %q", want)
		}
	}

	globalMark, err := parseArgs([]string{"mark", "all", "read"})
	if err != nil {
		t.Fatalf("parse global mark: %v", err)
	}
	if globalMark.Command != "mra" || globalMark.Preset != "" {
		t.Fatalf("unexpected global mark parse: %+v", globalMark)
	}
	presetMark, err := parseArgs([]string{"2", "mark", "all", "read"})
	if err != nil {
		t.Fatalf("parse preset mark: %v", err)
	}
	if presetMark.Command != "mra" || presetMark.Preset != "2" {
		t.Fatalf("unexpected preset mark parse: %+v", presetMark)
	}
	if _, err := parseArgs([]string{"mark", "read"}); err == nil {
		t.Fatalf("expected malformed mark to fail")
	}

	channelsArgs, err := parseArgs([]string{"2", "list", "channels"})
	if err != nil {
		t.Fatalf("parse list channels: %v", err)
	}
	if channelsArgs.Command != "list-channels" || channelsArgs.Preset != "2" {
		t.Fatalf("unexpected list channels parse: %+v", channelsArgs)
	}
	dmsJSON, err := parseArgs([]string{"2", "list", "dms", "output", "json"})
	if err != nil || dmsJSON.Command != "list-dms" || !dmsJSON.OutputJSON {
		t.Fatalf("unexpected list dms json parse: %+v err=%v", dmsJSON, err)
	}
	contactsArgs, err := parseArgs([]string{"1", "list", "contacts"})
	if err != nil || contactsArgs.Command != "list-contacts" {
		t.Fatalf("unexpected list contacts parse: %+v err=%v", contactsArgs, err)
	}
	if _, err := parseArgs([]string{"2", "conversations", "list"}); err == nil {
		t.Fatalf("expected conversations list to be rejected")
	}
	if _, err := parseArgs([]string{"2", "contacts", "list"}); err == nil {
		t.Fatalf("expected contacts list to be rejected")
	}
	cleanArgs, err := parseArgs([]string{"2", "conversations", "clean"})
	if err != nil || cleanArgs.Command != "sc" {
		t.Fatalf("unexpected conversations clean parse: %+v err=%v", cleanArgs, err)
	}
}

func TestChannelNameQuery(t *testing.T) {
	if got := channelNameQuery("#blog"); got != "blog" {
		t.Fatalf("channelNameQuery(#blog)=%q", got)
	}
	if got := channelNameQuery("C0123AB"); got != "" {
		t.Fatalf("channelNameQuery(channel id)=%q", got)
	}
}

func TestPostSendUsesBatchUploadWithoutThread(t *testing.T) {
	var completePayload map[string]string
	var uploadURL string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/files.getUploadURLExternal":
			_, _ = w.Write([]byte(`{"ok":true,"upload_url":"` + uploadURL + `","file_id":"F1"}`))
		case "/upload":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`ok`))
		case "/api/files.completeUploadExternal":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &completePayload)
			_, _ = w.Write([]byte(`{"ok":true,"files":[{"shares":{"C123":[{"ts":"200.1"}]}}]}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()
	uploadURL = server.URL + "/upload"

	attachPath := filepath.Join(t.TempDir(), "note.txt")
	if err := os.WriteFile(attachPath, []byte("hi"), 0o644); err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"bot":"xoxb-token"}}}}`), 0o600); err != nil {
		t.Fatal(err)
	}

	oldBase := slackAPIBase
	slackAPIBase = server.URL + "/api/"
	defer func() { slackAPIBase = oldBase }()

	var stdout bytes.Buffer
	rt := NewRuntime()
	rt.Stdout = &stdout
	rt.Stderr = &bytes.Buffer{}
	rt.HTTPClient = server.Client()

	err := rt.Run([]string{"1", "send", "to", "C123", "body", "caption", "attach", attachPath})
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	if completePayload["thread_ts"] != "" {
		t.Fatalf("expected no thread_ts, got %#v", completePayload)
	}
	if completePayload["initial_comment"] != "caption" {
		t.Fatalf("expected initial_comment, got %#v", completePayload)
	}
	if !strings.Contains(stdout.String(), "posted target=C123") {
		t.Fatalf("stdout: %s", stdout.String())
	}
}

func TestAuthStoresTokensInsidePreset(t *testing.T) {
	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	var stdout bytes.Buffer
	rt := NewRuntime()
	rt.Stdout = &stdout
	rt.Stderr = &bytes.Buffer{}

	err := rt.Run([]string{"auth", "2", "bot", "xoxb-bot", "user", "xoxp-user", "app", "xapp-app", "name", "work"})
	if err != nil {
		t.Fatalf("auth: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(home, "config", "slack", "config.json"))
	if err != nil {
		t.Fatalf("read config: %v", err)
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		t.Fatalf("decode config: %v", err)
	}
	account := accounts(cfg)["2"]
	tokens := tokenMap(account)
	if tokens["bot"] != "xoxb-bot" || tokens["user"] != "xoxp-user" || tokens["app"] != "xapp-app" {
		t.Fatalf("unexpected tokens: %#v", tokens)
	}
	if account["name"] != "work" {
		t.Fatalf("unexpected account name: %#v", account["name"])
	}
}

func TestMarkAllReadUsesUserTokenAndCache(t *testing.T) {
	var calls []map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer xoxp-token" {
			t.Fatalf("unexpected auth header: %s", got)
		}
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/users.conversations":
			_, _ = w.Write([]byte(`{"ok":true,"channels":[]}`))
		case "/api/conversations.mark":
			if err := r.ParseForm(); err != nil {
				t.Fatal(err)
			}
			calls = append(calls, map[string]string{"channel": r.Form.Get("channel"), "ts": r.Form.Get("ts")})
			_, _ = w.Write([]byte(`{"ok":true}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	cachePath := filepath.Join(t.TempDir(), "events.db")
	_, err := eventCacheStoreEntries(cachePath, []MessageEntry{
		{
			SortTS:       100.0001,
			Email:        "maanas@example.com",
			DMID:         "D1",
			ChannelID:    "D1",
			Surface:      "dm",
			Conversation: "Maanas",
			UserID:       "U2",
			Members:      "-",
			Message:      map[string]any{"ts": "100.000100", "user": "U2", "text": "cached dm"},
			Sender:       map[string]any{"id": "U2", "name": "Maanas", "email": "maanas@example.com", "label": "Maanas"},
			Unread:       true,
		},
		{
			SortTS:       101.0001,
			Email:        "-",
			DMID:         "G1",
			ChannelID:    "G1",
			Surface:      "group_dm",
			Conversation: "A, B",
			UserID:       "U3",
			Members:      "3",
			Message:      map[string]any{"ts": "101.000100", "user": "U3", "text": "cached group dm"},
			Sender:       map[string]any{"id": "U3", "name": "A", "email": "-", "label": "A"},
			Unread:       true,
		},
	}, "", true)
	if err != nil {
		t.Fatalf("store cache: %v", err)
	}

	var stdout bytes.Buffer
	rt := NewRuntime()
	rt.Stdout = &stdout
	rt.Stderr = &bytes.Buffer{}
	rt.HTTPClient = server.Client()
	client := SlackClient{Token: "xoxp-token", HTTPClient: server.Client()}
	oldTransportBase := slackAPIBase
	slackAPIBase = server.URL + "/api/"
	defer func() { slackAPIBase = oldTransportBase }()

	result, err := rt.markAllUnreadNotificationsAsRead(client, cachePath, "1")
	if err != nil {
		t.Fatalf("mark all read: %v", err)
	}
	if result.Marked != 2 || result.Failed != 0 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if len(calls) != 2 || calls[0]["channel"] != "G1" || calls[1]["channel"] != "D1" {
		t.Fatalf("unexpected mark calls: %#v", calls)
	}
	if !strings.Contains(stdout.String(), "Summary: marked_read=2 failed=0") {
		t.Fatalf("missing summary: %s", stdout.String())
	}
	entries, err := eventCacheLoadEntries(cachePath, "U1", 10, "")
	if err != nil {
		t.Fatalf("reload cache: %v", err)
	}
	for _, entry := range entries {
		if (entry.ChannelID == "D1" || entry.ChannelID == "G1") && entry.Unread {
			t.Fatalf("entry still unread: %+v", entry)
		}
	}
}
