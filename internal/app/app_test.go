package app

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
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
		"slack <preset> delete message",
		"slack <preset> edit message",
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
