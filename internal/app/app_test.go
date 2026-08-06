package app

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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
		"slack <preset> delete message",
		"slack <preset> edit message",
		"slack <preset> reply to <message_id>",
		"slack <preset> thread <message_id>",
		"slack config | slack config edit",
		"slack mark all read",
		"output json",
		"in <channel>",
	} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("help missing %q\nhelp:\n%s", want, stdout.String())
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

	eventsSync, err := parseArgs([]string{"2", "events", "sync"})
	if err != nil || eventsSync.Command != "events" || eventsSync.EventsAction != "sync" {
		t.Fatalf("unexpected events sync parse: %+v err=%v", eventsSync, err)
	}
	eventsTimerDisable, err := parseArgs([]string{"2", "events", "timer", "disable"})
	if err != nil || eventsTimerDisable.Command != "events" || eventsTimerDisable.EventsAction != "timer-disable" {
		t.Fatalf("unexpected legacy timer disable parse: %+v err=%v", eventsTimerDisable, err)
	}
	for _, argv := range [][]string{
		{"2", "events", "once"},
		{"2", "events", "service"},
		{"2", "events", "logs"},
		{"2", "events", "timer", "install"},
	} {
		if _, err := parseArgs(argv); err == nil {
			t.Fatalf("expected removed event command to fail: %v", argv)
		}
	}
}

func TestUpgradeUsesCommandHook(t *testing.T) {
	var commandName string
	var commandArgs []string
	rt := NewRuntime()
	rt.Stdout = &bytes.Buffer{}
	rt.Stderr = &bytes.Buffer{}
	rt.RunCommand = func(name string, args ...string) error {
		commandName = name
		commandArgs = append([]string{}, args...)
		return nil
	}

	if err := rt.Run([]string{"upgrade"}); err != nil {
		t.Fatalf("upgrade: %v", err)
	}
	if commandName != "bash" || len(commandArgs) != 2 || commandArgs[0] != "-c" {
		t.Fatalf("unexpected upgrade command: %s %#v", commandName, commandArgs)
	}
	if !strings.Contains(commandArgs[1], installScriptURL) || !strings.Contains(commandArgs[1], "upgrade") {
		t.Fatalf("unexpected upgrade shell: %s", commandArgs[1])
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
	var completeContentType string
	var getUploadAuth string
	var getUploadContentType string
	var getUploadForm url.Values
	var completeAuth string
	var uploadBody []byte
	var uploadContentType string
	var uploadURL string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/files.getUploadURLExternal":
			getUploadAuth = r.Header.Get("Authorization")
			getUploadContentType = r.Header.Get("Content-Type")
			if err := r.ParseForm(); err != nil {
				t.Fatalf("parse getUpload form: %v", err)
			}
			getUploadForm = r.Form
			_, _ = w.Write([]byte(`{"ok":true,"upload_url":"` + uploadURL + `","file_id":"F1"}`))
		case "/upload":
			uploadContentType = r.Header.Get("Content-Type")
			uploadBody, _ = io.ReadAll(r.Body)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`ok`))
		case "/api/files.completeUploadExternal":
			completeAuth = r.Header.Get("Authorization")
			completeContentType = r.Header.Get("Content-Type")
			if err := r.ParseForm(); err != nil {
				t.Fatalf("parse complete form: %v", err)
			}
			completePayload = map[string]string{}
			for key, values := range r.Form {
				if len(values) > 0 {
					completePayload[key] = values[0]
				}
			}
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
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"bot":"xoxb-token","user":"xoxp-user"}}}}`), 0o600); err != nil {
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
	if getUploadAuth != "Bearer xoxp-user" || completeAuth != "Bearer xoxp-user" {
		t.Fatalf("expected user token for file upload, get=%q complete=%q", getUploadAuth, completeAuth)
	}
	if !strings.HasPrefix(getUploadContentType, "application/x-www-form-urlencoded") {
		t.Fatalf("expected getUpload form encoding, got %q", getUploadContentType)
	}
	if getUploadForm.Get("filename") != "note.txt" || getUploadForm.Get("length") != "2" {
		t.Fatalf("unexpected getUpload form: %#v", getUploadForm)
	}
	if string(uploadBody) != "hi" {
		t.Fatalf("expected raw upload bytes, got %q", string(uploadBody))
	}
	if strings.HasPrefix(uploadContentType, "multipart/") {
		t.Fatalf("expected raw upload content type, got %q", uploadContentType)
	}
	if !strings.HasPrefix(completeContentType, "application/x-www-form-urlencoded") {
		t.Fatalf("expected completeUpload form encoding, got %q", completeContentType)
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

	err := rt.Run([]string{"auth", "2", "user", "xoxp-user", "bot", "xoxb-bot", "name", "work"})
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
	if tokens["bot"] != "xoxb-bot" || tokens["user"] != "xoxp-user" {
		t.Fatalf("unexpected tokens: %#v", tokens)
	}
	if account["name"] != "work" {
		t.Fatalf("unexpected account name: %#v", account["name"])
	}
}

func TestInspectConversationUsesUserToken(t *testing.T) {
	longText := strings.Repeat("alpha ", 80)
	var authHeader string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader = r.Header.Get("Authorization")
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/conversations.history":
			payload := map[string]any{
				"ok": true,
				"messages": []map[string]any{{
					"ts":   "100.1",
					"user": "U2",
					"text": longText,
				}},
			}
			data, _ := json.Marshal(payload)
			_, _ = w.Write(data)
		case "/api/users.info":
			_, _ = w.Write([]byte(`{"ok":true,"user":{"id":"U2","profile":{"real_name":"Mike Willbanks","email":"mike@willbanks.dev"}}}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"bot":"xoxb-bot","user":"xoxp-user"}}}}`), 0o600); err != nil {
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

	err := rt.Run([]string{"1", "inspect", "conversation", "C0B7V41SRST"})
	if err != nil {
		t.Fatalf("inspect conversation: %v", err)
	}
	if authHeader != "Bearer xoxp-user" {
		t.Fatalf("expected user token, got %q", authHeader)
	}
	if !strings.Contains(stdout.String(), strings.TrimSpace(longText)) {
		t.Fatalf("inspect should include full text, stdout: %s", stdout.String())
	}
	if strings.Contains(stdout.String(), "...") {
		t.Fatalf("inspect should not truncate text, stdout: %s", stdout.String())
	}
}

func TestSenderFilterResolvesSavedContact(t *testing.T) {
	contacts := Contacts{"mike": "mike@willbanks.dev"}
	terms := senderFilterTerms(contacts, "mike")
	if len(terms) < 2 {
		t.Fatalf("expected multiple sender terms, got %#v", terms)
	}
	if resolveSenderSearchTerm(contacts, "mike") != "mike@willbanks.dev" {
		t.Fatalf("unexpected search term: %s", resolveSenderSearchTerm(contacts, "mike"))
	}
	entry := MessageEntry{
		Sender:  map[string]any{"name": "Mike Willbanks", "email": "mike@willbanks.dev", "label": "Mike Willbanks", "id": "U2"},
		Message: map[string]any{"text": "works for me"},
	}
	if !entryPassesFilters(entry, contacts, "", "mike", "", 0, false) {
		t.Fatal("expected contact label to match sender")
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

func TestChannelPostUsesUserToken(t *testing.T) {
	var authHeader string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader = r.Header.Get("Authorization")
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/chat.postMessage":
			_, _ = w.Write([]byte(`{"ok":true,"ts":"200.1"}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"bot":"xoxb-bot","user":"xoxp-user"}}}}`), 0o600); err != nil {
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

	err := rt.Run([]string{"1", "send", "to", "C0B7V41SRST", "body", "engineering update"})
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	if authHeader != "Bearer xoxp-user" {
		t.Fatalf("expected user token for channel post, got %q", authHeader)
	}
	if !strings.Contains(stdout.String(), "posted target=C0B7V41SRST") {
		t.Fatalf("stdout: %s", stdout.String())
	}
}

func TestDeleteAndEditMessage(t *testing.T) {
	var deletePayload map[string]string
	var editPayload map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/chat.delete":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &deletePayload)
			_, _ = w.Write([]byte(`{"ok":true}`))
		case "/api/chat.update":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &editPayload)
			_, _ = w.Write([]byte(`{"ok":true,"ts":"200.1"}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"bot":"xoxb-bot","user":"xoxp-user"}}}}`), 0o600); err != nil {
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

	err := rt.Run([]string{"1", "delete", "message", "C0B7V41SRST:1781778512.813869"})
	if err != nil {
		t.Fatalf("delete: %v", err)
	}
	if deletePayload["channel"] != "C0B7V41SRST" || deletePayload["ts"] != "1781778512.813869" {
		t.Fatalf("unexpected delete payload: %#v", deletePayload)
	}
	if !strings.Contains(stdout.String(), "deleted message_id=C0B7V41SRST:1781778512.813869") {
		t.Fatalf("delete stdout: %s", stdout.String())
	}

	stdout.Reset()
	err = rt.Run([]string{"1", "edit", "message", "C0B7V41SRST:1781778811.092529", "body", "corrected update"})
	if err != nil {
		t.Fatalf("edit: %v", err)
	}
	if editPayload["channel"] != "C0B7V41SRST" || editPayload["ts"] != "1781778811.092529" || editPayload["text"] != "corrected update" {
		t.Fatalf("unexpected edit payload: %#v", editPayload)
	}
	if !strings.Contains(stdout.String(), "edited message_id=C0B7V41SRST:1781778811.092529") {
		t.Fatalf("edit stdout: %s", stdout.String())
	}
}

func TestDeleteEditParseContract(t *testing.T) {
	deleteArgs, err := parseArgs([]string{"2", "delete", "message", "C0B7V41SRST:1781778512.813869"})
	if err != nil || deleteArgs.Command != "delete" || deleteArgs.Recipient != "C0B7V41SRST:1781778512.813869" {
		t.Fatalf("unexpected delete parse: %+v err=%v", deleteArgs, err)
	}
	editArgs, err := parseArgs([]string{"2", "preview", "edit", "message", "C0B7V41SRST:1781778811.092529", "body", "updated"})
	if err != nil || editArgs.Command != "preview-edit" || editArgs.Message != "updated" {
		t.Fatalf("unexpected preview edit parse: %+v err=%v", editArgs, err)
	}
	if _, err := parseArgs([]string{"2", "delete", "message", "bad-id"}); err == nil {
		t.Fatal("expected invalid message id to fail")
	}
}

func TestReplyParseIsExecutable(t *testing.T) {
	// Regression: reply was incorrectly treated as a retired alias, so
	// preview reply worked while real reply was rejected.
	replyArgs, err := parseArgs([]string{"1", "reply", "to", "C123ABC:1712764800.000100", "body", "Example"})
	if err != nil {
		t.Fatalf("parse reply: %v", err)
	}
	if replyArgs.Command != "reply" || replyArgs.Recipient != "C123ABC:1712764800.000100" || replyArgs.Message != "Example" {
		t.Fatalf("unexpected reply parse: %+v", replyArgs)
	}
	previewArgs, err := parseArgs([]string{"1", "preview", "reply", "to", "C123ABC:1712764800.000100", "body", "Example"})
	if err != nil || previewArgs.Command != "preview-reply" {
		t.Fatalf("unexpected preview reply parse: %+v err=%v", previewArgs, err)
	}
	if _, err := parseArgs([]string{"1", "reply", "to", "not-a-message", "body", "x"}); err == nil {
		t.Fatal("expected invalid reply target to fail")
	}
}

func TestListChannelAndSinceParse(t *testing.T) {
	args, err := parseArgs([]string{"1", "list", "in", "#genie", "from", "ryan", "since", "4h", "limit", "20", "output", "json"})
	if err != nil {
		t.Fatalf("parse list in/since: %v", err)
	}
	if args.Command != "ls" || args.ListIn != "#genie" || args.ListFrom != "ryan" || args.ListTimeLimit != "4h" || args.ListLimit != 20 || !args.OutputJSON {
		t.Fatalf("unexpected list parse: %+v", args)
	}
	if _, err := parseArgs([]string{"1", "list", "since", "not-a-window"}); err == nil {
		t.Fatal("expected invalid since window to fail")
	}
	threadArgs, err := parseArgs([]string{"1", "thread", "C123ABC:1712764800.000100", "limit", "25", "output", "json"})
	if err != nil || threadArgs.Command != "thread" || threadArgs.ListLimit != 25 || !threadArgs.OutputJSON {
		t.Fatalf("unexpected thread parse: %+v err=%v", threadArgs, err)
	}
	configArgs, err := parseArgs([]string{"config"})
	if err != nil || configArgs.Command != "config" {
		t.Fatalf("unexpected config parse: %+v err=%v", configArgs, err)
	}
	editArgs, err := parseArgs([]string{"config", "edit"})
	if err != nil || editArgs.Command != "config-edit" {
		t.Fatalf("unexpected config edit parse: %+v err=%v", editArgs, err)
	}
}

func TestStartTSSupportsHours(t *testing.T) {
	cutoff, ok := startTS("4h")
	if !ok {
		t.Fatal("expected 4h to parse")
	}
	now := float64(time.Now().Unix())
	if cutoff < now-4*3600-5 || cutoff > now-4*3600+5 {
		t.Fatalf("unexpected 4h cutoff: %v (now=%v)", cutoff, now)
	}
	if _, ok := startTS("0h"); ok {
		t.Fatal("expected 0h to be invalid")
	}
	if _, ok := startTS("banana"); ok {
		t.Fatal("expected banana to be invalid")
	}
}

func TestConfigDefaultsToRedactedSummary(t *testing.T) {
	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"name":"work","token":{"user":"xoxp-super-secret-token","bot":"xoxb-bot-secret"}}}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var stdout bytes.Buffer
	rt := NewRuntime()
	rt.Stdout = &stdout
	rt.Stderr = &bytes.Buffer{}
	if err := rt.Run([]string{"config"}); err != nil {
		t.Fatalf("config: %v", err)
	}
	out := stdout.String()
	if strings.Contains(out, "xoxp-super-secret-token") || strings.Contains(out, "xoxb-bot-secret") {
		t.Fatalf("config leaked tokens: %s", out)
	}
	if !strings.Contains(out, "has_user_token: true") || !strings.Contains(out, "preset: 1") {
		t.Fatalf("expected redacted summary, got: %s", out)
	}
}

func TestConfigEditRequiresTTY(t *testing.T) {
	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	var stdout bytes.Buffer
	rt := NewRuntime()
	rt.Stdout = &stdout
	rt.Stderr = &bytes.Buffer{}
	rt.IsTTY = func() bool { return false }
	opened := false
	rt.OpenEditor = func(path string, bootstrap string) error {
		opened = true
		return nil
	}
	err := rt.Run([]string{"config", "edit"})
	if err == nil {
		t.Fatal("expected config edit without TTY to fail")
	}
	if opened {
		t.Fatal("editor must not open without TTY")
	}
	if !strings.Contains(err.Error(), "interactive TTY") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestReplyExecutionPostsThread(t *testing.T) {
	var postPayload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/conversations.replies":
			_, _ = w.Write([]byte(`{"ok":true,"messages":[{"ts":"1712764800.000100","thread_ts":"1712764800.000100","text":"root"}]}`))
		case "/api/conversations.history":
			_, _ = w.Write([]byte(`{"ok":true,"messages":[{"ts":"1712764800.000100","thread_ts":"1712764800.000100","text":"root"}]}`))
		case "/api/chat.postMessage":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &postPayload)
			_, _ = w.Write([]byte(`{"ok":true,"ts":"1712764801.000200"}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"user":"xoxp-user"}}}}`), 0o600); err != nil {
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
	err := rt.Run([]string{"1", "reply", "to", "C123ABC:1712764800.000100", "body", "Example"})
	if err != nil {
		t.Fatalf("reply: %v", err)
	}
	if str(postPayload["channel"]) != "C123ABC" || str(postPayload["thread_ts"]) != "1712764800.000100" || str(postPayload["text"]) != "Example" {
		t.Fatalf("unexpected post payload: %#v", postPayload)
	}
	if !strings.Contains(stdout.String(), "replied message_id=C123ABC:1712764800.000100") {
		t.Fatalf("stdout: %s", stdout.String())
	}
}

func TestThreadCommandListsReplies(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/conversations.replies":
			_, _ = w.Write([]byte(`{"ok":true,"messages":[
				{"ts":"1712764800.000100","thread_ts":"1712764800.000100","user":"U2","text":"root","reply_count":1},
				{"ts":"1712764801.000200","thread_ts":"1712764800.000100","user":"U3","text":"reply"}
			]}`))
		case "/api/users.info":
			_, _ = w.Write([]byte(`{"ok":true,"user":{"id":"U2","profile":{"real_name":"Ada"}}}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"user":"xoxp-user"}}}}`), 0o600); err != nil {
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
	err := rt.Run([]string{"1", "thread", "C123ABC:1712764800.000100", "output", "json"})
	if err != nil {
		t.Fatalf("thread: %v", err)
	}
	var payload map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &payload); err != nil {
		t.Fatalf("json: %v stdout=%s", err, stdout.String())
	}
	if str(payload["thread_ts"]) != "1712764800.000100" {
		t.Fatalf("payload: %#v", payload)
	}
	messages := asList(payload["messages"])
	if len(messages) != 2 {
		t.Fatalf("expected 2 messages, got %#v", payload)
	}
}

func TestListInChannelUsesHistoryOldest(t *testing.T) {
	now := time.Now().Unix()
	newTS := fmt.Sprintf("%d.100000", now-60)
	oldTS := fmt.Sprintf("%d.100000", now-120)
	var historyQuery url.Values
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth.test":
			_, _ = w.Write([]byte(`{"ok":true,"user_id":"U1"}`))
		case "/api/conversations.history":
			historyQuery = r.URL.Query()
			payload := fmt.Sprintf(`{"ok":true,"messages":[
				{"ts":%q,"user":"U2","text":"new"},
				{"ts":%q,"user":"U2","text":"old"}
			]}`, newTS, oldTS)
			_, _ = w.Write([]byte(payload))
		case "/api/conversations.info":
			_, _ = w.Write([]byte(`{"ok":true,"channel":{"id":"C123ABC","name":"genie","is_channel":true}}`))
		case "/api/users.info":
			_, _ = w.Write([]byte(`{"ok":true,"user":{"id":"U2","profile":{"real_name":"Ada"}}}`))
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	configPath := filepath.Join(home, "config", "slack", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte(`{"accounts":{"1":{"token":{"user":"xoxp-user"}}}}`), 0o600); err != nil {
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
	err := rt.Run([]string{"1", "list", "in", "C123ABC", "since", "4h", "limit", "10", "output", "json"})
	if err != nil {
		t.Fatalf("list in: %v", err)
	}
	if historyQuery.Get("channel") != "C123ABC" || historyQuery.Get("oldest") == "" {
		t.Fatalf("expected channel+oldest history query, got %#v", historyQuery)
	}
	var payload map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &payload); err != nil {
		t.Fatalf("json: %v stdout=%s", err, stdout.String())
	}
	if str(payload["order"]) != "newest_first" {
		t.Fatalf("expected newest_first, got %#v", payload)
	}
	messages := asList(payload["messages"])
	if len(messages) == 0 {
		t.Fatalf("expected messages, got %#v", payload)
	}
	first := asMap(messages[0])
	if str(first["ts"]) != newTS {
		t.Fatalf("expected newest first, got %#v", messages)
	}
}
