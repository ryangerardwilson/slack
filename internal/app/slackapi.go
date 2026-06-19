package app

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type SlackClient struct {
	Token      string
	HTTPClient *http.Client
}

var slackAPIBase = "https://slack.com/api/"

type PostTarget struct {
	Kind      string
	ChannelID string
	UserID    string
}

type MessageEntry struct {
	SortTS       float64        `json:"sort_ts"`
	Email        string         `json:"email"`
	DMID         string         `json:"dm_id"`
	ChannelID    string         `json:"channel_id"`
	Surface      string         `json:"surface"`
	Conversation string         `json:"conversation"`
	UserID       string         `json:"user_id"`
	Members      string         `json:"members"`
	Message      map[string]any `json:"message"`
	Sender       map[string]any `json:"sender"`
	Unread       bool           `json:"unread"`
}

type ConversationRow struct {
	ChannelID     string
	Surface       string
	Conversation  string
	Name          string
	Email         string
	Members       string
	UserID        string
	LatestTS      string
	LastRead      string
	Unread        int
	HistoryLoaded bool
	Info          map[string]any
	Messages      []MessageEntry
}

func tokenKind(token string) string {
	switch {
	case strings.HasPrefix(token, "xapp-"):
		return "app"
	case strings.HasPrefix(token, "xoxb-"):
		return "bot"
	case strings.HasPrefix(token, "xoxp-"):
		return "user"
	default:
		return "unknown"
	}
}

func readTokenFile(path string) string {
	path = expandPath(path)
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func tokenMap(cfg map[string]any) map[string]string {
	out := map[string]string{}
	raw := asMap(cfg["token"])
	for _, kind := range []string{"bot", "user", "app"} {
		if value := strings.TrimSpace(str(raw[kind])); value != "" {
			out[kind] = value
		}
	}
	legacy := map[string][]string{
		"bot":  {"bot_token", "slack_bot_token"},
		"user": {"user_token", "slack_user_token", "token"},
		"app":  {"app_token", "socket_token"},
	}
	for kind, keys := range legacy {
		if out[kind] != "" {
			continue
		}
		for _, key := range keys {
			if value := strings.TrimSpace(str(cfg[key])); value != "" {
				out[kind] = value
				break
			}
		}
	}
	return out
}

func directToken(cfg map[string]any, keys ...string) string {
	tokens := tokenMap(cfg)
	for _, key := range keys {
		if value := strings.TrimSpace(tokens[key]); value != "" {
			return value
		}
		if value := strings.TrimSpace(str(cfg[key])); value != "" {
			return value
		}
	}
	return ""
}

func hasToken(cfg map[string]any, kind string) bool {
	return directToken(cfg, kind) != ""
}

func resolveToken(cfg map[string]any) (string, error) {
	token := firstNonEmpty(
		directToken(cfg, "bot"),
		getenv("SLACK_BOT_TOKEN"),
		getenv("SLACK_TOKEN"),
		directToken(cfg, "user"),
		readTokenFile(defaultBotTokenFile),
		readTokenFile(defaultUserTokenFile),
	)
	if token == "" {
		return "", UsageError{Message: "Missing Slack token. Set SLACK_BOT_TOKEN or add a token in slack config."}
	}
	if tokenKind(token) == "unknown" {
		return "", UsageError{Message: "Slack token must be a bot token (xoxb-) or user token (xoxp-)."}
	}
	return token, nil
}

func resolveListToken(cfg map[string]any) (string, error) {
	token := firstNonEmpty(
		directToken(cfg, "user"),
		getenv("SLACK_USER_TOKEN"),
		getenv("SLACK_TOKEN"),
		directToken(cfg, "bot"),
		readTokenFile(defaultUserTokenFile),
		readTokenFile(defaultBotTokenFile),
	)
	if token == "" {
		return "", UsageError{Message: "Missing Slack token. For message listing, add a user token in slack config or set SLACK_TOKEN."}
	}
	if tokenKind(token) == "unknown" {
		return "", UsageError{Message: "Slack token must be a bot token (xoxb-) or user token (xoxp-)."}
	}
	return token, nil
}

func resolveLookupToken(cfg map[string]any, fallback string) (string, error) {
	token := firstNonEmpty(directToken(cfg, "user"), getenv("SLACK_USER_TOKEN"), readTokenFile(defaultUserTokenFile), fallback)
	if token != "" && tokenKind(token) == "unknown" {
		return "", UsageError{Message: "Slack token must be a bot token (xoxb-) or user token (xoxp-)."}
	}
	return token, nil
}

func resolveDirectPostToken(cfg map[string]any, fallback string) string {
	return firstNonEmpty(directToken(cfg, "user"), getenv("SLACK_USER_TOKEN"), readTokenFile(defaultUserTokenFile), fallback)
}

func resolveMarkReadToken(cfg map[string]any) (string, error) {
	token, err := resolveListToken(cfg)
	if err != nil {
		return "", err
	}
	if tokenKind(token) != "user" {
		return "", UsageError{Message: "mark all read requires a user token (xoxp-) with im:write, mpim:write, groups:write, and channels:write for every conversation type."}
	}
	return token, nil
}

func resolveAppToken(cfg map[string]any) (string, error) {
	token := firstNonEmpty(directToken(cfg, "app"), getenv("SLACK_APP_TOKEN"), readTokenFile(defaultAppTokenFile))
	if token == "" {
		return "", UsageError{Message: "Missing Slack app token. Add app token in slack config or import ~/.openclaw/credentials/slack-app-token with slack auth <preset> import."}
	}
	if tokenKind(token) != "app" {
		return "", UsageError{Message: "Slack app token must start with xapp-."}
	}
	return token, nil
}

func (rt *Runtime) slackClient(token string) SlackClient {
	client := rt.HTTPClient
	if client == nil {
		client = http.DefaultClient
	}
	return SlackClient{Token: token, HTTPClient: client}
}

func (c SlackClient) Request(method string, payload map[string]string, useForm bool, httpMethod string, allowError bool) (map[string]any, error) {
	if httpMethod == "" {
		httpMethod = http.MethodPost
	}
	endpoint := slackAPIBase + method
	var body io.Reader
	reqURL := endpoint
	contentType := "application/json; charset=utf-8"
	if strings.EqualFold(httpMethod, http.MethodGet) {
		values := url.Values{}
		for key, value := range payload {
			values.Set(key, value)
		}
		if encoded := values.Encode(); encoded != "" {
			reqURL += "?" + encoded
		}
	} else if useForm {
		values := url.Values{}
		for key, value := range payload {
			values.Set(key, value)
		}
		body = strings.NewReader(values.Encode())
		contentType = "application/x-www-form-urlencoded"
	} else {
		data, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequest(httpMethod, reqURL, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("Content-Type", contentType)
	req.Header.Set("Accept", "application/json")
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	var decoded map[string]any
	if err := json.Unmarshal(data, &decoded); err != nil {
		return nil, fmt.Errorf("Slack API %s returned invalid JSON: %w", method, err)
	}
	if decoded["ok"] != true && !allowError {
		errText := firstNonEmpty(str(decoded["error"]), "unknown_error")
		return decoded, fmt.Errorf("Slack API %s failed: %s", method, errText)
	}
	return decoded, nil
}

func (c SlackClient) AuthTest() (map[string]any, error) {
	return c.Request("auth.test", map[string]string{}, false, http.MethodPost, false)
}

func listAPI(client SlackClient, method string, params map[string]string, key string) ([]map[string]any, error) {
	var rows []map[string]any
	cursor := ""
	for {
		payload := map[string]string{}
		for k, v := range params {
			payload[k] = v
		}
		if cursor != "" {
			payload["cursor"] = cursor
		}
		data, err := client.Request(method, payload, false, http.MethodGet, false)
		if err != nil {
			return nil, err
		}
		for _, raw := range asList(data[key]) {
			if item := asMap(raw); len(item) > 0 {
				rows = append(rows, item)
			}
		}
		cursor = strings.TrimSpace(str(asMap(data["response_metadata"])["next_cursor"]))
		if cursor == "" {
			break
		}
	}
	return rows, nil
}

func channelNameQuery(value string) string {
	raw := strings.TrimSpace(value)
	if raw == "" {
		return ""
	}
	if strings.HasPrefix(raw, "#") {
		raw = strings.TrimSpace(raw[1:])
	}
	if raw == "" || strings.Contains(raw, "@") || strings.Contains(raw, ":") {
		return ""
	}
	if conversationIDRE.MatchString(raw) || userIDRE.MatchString(raw) {
		return ""
	}
	return strings.ToLower(raw)
}

func listMemberChannels(client SlackClient) ([]map[string]any, error) {
	return listAPI(client, "users.conversations", map[string]string{
		"types":            "public_channel,private_channel",
		"exclude_archived": "true",
		"limit":            "200",
	}, "channels")
}

func listMemberDMs(client SlackClient) ([]map[string]any, error) {
	return listAPI(client, "users.conversations", map[string]string{
		"types":            "im,mpim",
		"exclude_archived": "true",
		"limit":            "200",
	}, "channels")
}

func lookupChannelIDByName(client SlackClient, value string) (string, error) {
	query := channelNameQuery(value)
	if query == "" {
		return "", nil
	}
	channels, err := listMemberChannels(client)
	if err != nil {
		return "", err
	}
	var matches []map[string]any
	for _, channel := range channels {
		name := strings.ToLower(firstNonEmpty(str(channel["name"]), str(channel["name_normalized"])))
		if name == query {
			matches = append(matches, channel)
		}
	}
	if len(matches) == 0 {
		return "", nil
	}
	if len(matches) > 1 {
		ids := make([]string, 0, len(matches))
		for _, channel := range matches {
			ids = append(ids, str(channel["id"]))
		}
		sort.Strings(ids)
		return "", UsageError{Message: fmt.Sprintf("Multiple Slack channels named %q. Use an explicit channel id: %s", query, strings.Join(ids, ", "))}
	}
	return str(matches[0]["id"]), nil
}

func lookupUserIDByEmail(client SlackClient, email string) (string, error) {
	data, err := client.Request("users.lookupByEmail", map[string]string{"email": email}, false, http.MethodGet, false)
	if err != nil {
		return "", err
	}
	user := asMap(data["user"])
	id := str(user["id"])
	if id == "" {
		return "", fmt.Errorf("No Slack user id found for email: %s", email)
	}
	return id, nil
}

func getUserInfo(client SlackClient, userID string) (map[string]any, error) {
	data, err := client.Request("users.info", map[string]string{"user": userID}, false, http.MethodGet, false)
	if err != nil {
		return nil, err
	}
	user := asMap(data["user"])
	if len(user) == 0 {
		return nil, fmt.Errorf("No Slack user found for id: %s", userID)
	}
	return user, nil
}

func openDM(client SlackClient, userID string) (string, error) {
	data, err := client.Request("conversations.open", map[string]string{"users": userID}, false, http.MethodPost, false)
	if err != nil {
		return "", err
	}
	channelID := str(asMap(data["channel"])["id"])
	if channelID == "" {
		return "", fmt.Errorf("Unable to open Slack DM with user: %s", userID)
	}
	return channelID, nil
}

func resolvePostTarget(rt *Runtime, recipient string, contacts Contacts, postClient SlackClient, lookupClient SlackClient, directClient SlackClient) (PostTarget, error) {
	value := strings.TrimSpace(recipient)
	if mapped := contacts[value]; mapped != "" {
		value = mapped
	}
	if channelID, _, ok := parseMessageID(value); ok {
		return PostTarget{Kind: "message", ChannelID: channelID}, nil
	}
	if conversationIDRE.MatchString(value) {
		return PostTarget{Kind: "channel", ChannelID: value}, nil
	}
	if userIDRE.MatchString(value) {
		channelID, err := openDM(directClient, value)
		if err != nil {
			return PostTarget{}, err
		}
		return PostTarget{Kind: "user", ChannelID: channelID, UserID: value}, nil
	}
	if channelID, err := lookupChannelIDByName(lookupClient, value); err != nil {
		return PostTarget{}, err
	} else if channelID != "" {
		return PostTarget{Kind: "channel_name", ChannelID: channelID}, nil
	}
	if strings.Contains(value, "@") {
		userID, err := lookupUserIDByEmail(lookupClient, value)
		if err != nil {
			return PostTarget{}, err
		}
		channelID, err := openDM(directClient, userID)
		if err != nil {
			return PostTarget{}, err
		}
		return PostTarget{Kind: "email", ChannelID: channelID, UserID: userID}, nil
	}
	_ = rt
	_ = postClient
	return PostTarget{}, UsageError{Message: fmt.Sprintf("Unable to resolve Slack target: %s (use contact, email, #channel, channel id, or message id)", recipient)}
}

func sendPost(client SlackClient, channelID, text, threadTS string) (string, error) {
	payload := map[string]string{
		"channel": channelID,
		"text":    text,
	}
	if threadTS != "" {
		payload["thread_ts"] = threadTS
	}
	data, err := client.Request("chat.postMessage", payload, false, http.MethodPost, false)
	if err != nil {
		return "", err
	}
	return str(data["ts"]), nil
}

func deleteMessage(client SlackClient, channelID, messageTS string) error {
	_, err := client.Request("chat.delete", map[string]string{
		"channel": channelID,
		"ts":      messageTS,
	}, false, http.MethodPost, false)
	return err
}

func editMessage(client SlackClient, channelID, messageTS, text string) error {
	_, err := client.Request("chat.update", map[string]string{
		"channel": channelID,
		"ts":      messageTS,
		"text":    text,
	}, false, http.MethodPost, false)
	return err
}

func resolveReplyThreadTS(client SlackClient, channelID, messageTS string) (string, error) {
	data, err := client.Request("conversations.replies", map[string]string{
		"channel":   channelID,
		"ts":        messageTS,
		"limit":     "1",
		"inclusive": "true",
	}, false, http.MethodGet, false)
	if err != nil {
		return "", err
	}
	messages := asList(data["messages"])
	if len(messages) == 0 {
		return "", fmt.Errorf("Message not found: %s:%s", channelID, messageTS)
	}
	message := asMap(messages[0])
	threadTS := firstNonEmpty(str(message["thread_ts"]), str(message["ts"]), messageTS)
	return threadTS, nil
}

func expandExistingPath(path string, kind string) (string, error) {
	expanded := expandPath(path)
	info, err := os.Stat(expanded)
	if err != nil {
		return "", fmt.Errorf("%s path not found: %s", kind, path)
	}
	if info.IsDir() && kind != "attachment" {
		return "", fmt.Errorf("%s path is a directory: %s", kind, path)
	}
	return expanded, nil
}

func zipDirectory(dirPath string) (string, func(), error) {
	base := filepath.Base(dirPath)
	temp, err := os.CreateTemp("", base+"-*.zip")
	if err != nil {
		return "", nil, err
	}
	temp.Close()
	cleanup := func() { _ = os.Remove(temp.Name()) }
	err = zipDir(dirPath, temp.Name())
	if err != nil {
		cleanup()
		return "", nil, err
	}
	return temp.Name(), cleanup, nil
}

func uploadExternalFile(client SlackClient, channelID, threadTS, path, filename string) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	data, err := client.Request("files.getUploadURLExternal", map[string]string{
		"filename": filename,
		"length":   strconv.FormatInt(info.Size(), 10),
	}, true, http.MethodPost, false)
	if err != nil {
		return "", err
	}
	uploadURL := str(data["upload_url"])
	fileID := str(data["file_id"])
	if uploadURL == "" || fileID == "" {
		return "", fmt.Errorf("Slack did not return upload URL for %s", filename)
	}
	if err := uploadRawFile(client.HTTPClient, uploadURL, path, filename); err != nil {
		return "", err
	}
	filePayload, _ := json.Marshal([]map[string]string{{"id": fileID, "title": filename}})
	payload := map[string]string{
		"channel_id": channelID,
		"files":      string(filePayload),
	}
	if threadTS != "" {
		payload["thread_ts"] = threadTS
	}
	if _, err := client.Request("files.completeUploadExternal", payload, true, http.MethodPost, false); err != nil {
		return "", err
	}
	return fileID, nil
}

func uploadRawFile(httpClient *http.Client, uploadURL, path, filename string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	req, err := http.NewRequest(http.MethodPost, uploadURL, file)
	if err != nil {
		return err
	}
	if contentType := mime.TypeByExtension(filepath.Ext(filename)); contentType != "" {
		req.Header.Set("Content-Type", contentType)
	} else {
		req.Header.Set("Content-Type", "application/octet-stream")
	}
	if info, err := file.Stat(); err == nil {
		req.ContentLength = info.Size()
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		data, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("file upload failed: %s %s", resp.Status, strings.TrimSpace(string(data)))
	}
	return nil
}

func escapeQuotes(value string) string {
	return strings.ReplaceAll(value, `"`, `\"`)
}

func completeUploadExternalBatch(client SlackClient, channelID, threadTS, initialComment string, paths []string) ([]string, string, error) {
	if len(paths) == 0 {
		return nil, "", nil
	}
	type uploadJob struct {
		path     string
		filename string
		cleanup  func()
	}
	var jobs []uploadJob
	for _, rawPath := range paths {
		path, err := expandExistingPath(rawPath, "attachment")
		if err != nil {
			for _, job := range jobs {
				job.cleanup()
			}
			return nil, "", err
		}
		cleanup := func() {}
		info, err := os.Stat(path)
		if err != nil {
			for _, job := range jobs {
				job.cleanup()
			}
			return nil, "", err
		}
		filename := filepath.Base(path)
		if info.IsDir() {
			zipped, clean, err := zipDirectory(path)
			if err != nil {
				for _, job := range jobs {
					job.cleanup()
				}
				return nil, "", err
			}
			path = zipped
			cleanup = clean
			filename = filepath.Base(rawPath) + ".zip"
		}
		jobs = append(jobs, uploadJob{path: path, filename: filename, cleanup: cleanup})
	}
	defer func() {
		for _, job := range jobs {
			job.cleanup()
		}
	}()

	fileEntries := make([]map[string]string, 0, len(jobs))
	uploaded := make([]string, 0, len(jobs))
	for _, job := range jobs {
		info, err := os.Stat(job.path)
		if err != nil {
			return nil, "", err
		}
		data, err := client.Request("files.getUploadURLExternal", map[string]string{
			"filename": job.filename,
			"length":   strconv.FormatInt(info.Size(), 10),
		}, true, http.MethodPost, false)
		if err != nil {
			return nil, "", err
		}
		uploadURL := str(data["upload_url"])
		fileID := str(data["file_id"])
		if uploadURL == "" || fileID == "" {
			return nil, "", fmt.Errorf("Slack did not return upload URL for %s", job.filename)
		}
		if err := uploadRawFile(client.HTTPClient, uploadURL, job.path, job.filename); err != nil {
			return nil, "", err
		}
		fileEntries = append(fileEntries, map[string]string{"id": fileID, "title": job.filename})
		uploaded = append(uploaded, job.filename)
	}
	filesJSON, err := json.Marshal(fileEntries)
	if err != nil {
		return nil, "", err
	}
	payload := map[string]string{
		"channel_id": channelID,
		"files":      string(filesJSON),
	}
	if strings.TrimSpace(initialComment) != "" {
		payload["initial_comment"] = strings.TrimSpace(initialComment)
	}
	if threadTS != "" {
		payload["thread_ts"] = threadTS
	}
	data, err := client.Request("files.completeUploadExternal", payload, true, http.MethodPost, false)
	if err != nil {
		return nil, "", err
	}
	shareTS := ""
	for _, filePayload := range asList(data["files"]) {
		fileMap := asMap(filePayload)
		shares := asMap(fileMap["shares"])
		for _, shareList := range shares {
			for _, rawShare := range asList(shareList) {
				share := asMap(rawShare)
				if ts := str(share["ts"]); ts != "" {
					shareTS = ts
					break
				}
			}
			if shareTS != "" {
				break
			}
		}
		if shareTS != "" {
			break
		}
	}
	return uploaded, shareTS, nil
}

func sendAttachments(client SlackClient, channelID, threadTS string, paths []string) ([]string, error) {
	var uploaded []string
	for _, rawPath := range paths {
		path, err := expandExistingPath(rawPath, "attachment")
		if err != nil {
			return nil, err
		}
		cleanup := func() {}
		info, err := os.Stat(path)
		if err != nil {
			return nil, err
		}
		filename := filepath.Base(path)
		if info.IsDir() {
			zipped, clean, err := zipDirectory(path)
			if err != nil {
				return nil, err
			}
			path = zipped
			cleanup = clean
			filename = filepath.Base(rawPath) + ".zip"
		}
		fileID, err := uploadExternalFile(client, channelID, threadTS, path, filename)
		cleanup()
		if err != nil {
			return nil, err
		}
		uploaded = append(uploaded, fileID)
	}
	return uploaded, nil
}

func isMessageID(value string) bool {
	_, _, ok := parseMessageID(value)
	return ok
}

func parseMessageID(value string) (string, string, bool) {
	match := messageIDRE.FindStringSubmatch(value)
	if len(match) != 3 {
		return "", "", false
	}
	return match[1], match[2], true
}

func messageID(channelID, ts string) string {
	return channelID + ":" + ts
}

func tsFloat(value string) float64 {
	parsed, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
	if err != nil {
		return 0
	}
	return parsed
}

func extractTS(payload map[string]any) string {
	for _, key := range []string{"ts", "latest"} {
		if value := str(payload[key]); value != "" {
			return value
		}
	}
	if latest := asMap(payload["latest"]); len(latest) > 0 {
		return str(latest["ts"])
	}
	return ""
}

func messageText(message map[string]any) string {
	text := strings.TrimSpace(str(message["text"]))
	if text != "" {
		return text
	}
	if files := asList(message["files"]); len(files) > 0 {
		var names []string
		for _, raw := range files {
			file := asMap(raw)
			if name := firstNonEmpty(str(file["name"]), str(file["title"]), str(file["id"])); name != "" {
				names = append(names, name)
			}
		}
		if len(names) > 0 {
			return "files: " + strings.Join(names, ", ")
		}
	}
	return "-"
}

func messageFiles(message map[string]any) []map[string]any {
	var files []map[string]any
	for _, raw := range asList(message["files"]) {
		if file := asMap(raw); len(file) > 0 {
			files = append(files, file)
		}
	}
	return files
}

func summarizeAttachments(message map[string]any) string {
	files := messageFiles(message)
	if len(files) == 0 {
		return "-"
	}
	var names []string
	for _, file := range files {
		names = append(names, firstNonEmpty(str(file["name"]), str(file["title"]), str(file["id"]), "file"))
	}
	return strings.Join(names, ",")
}

func formatTS(value string) string {
	seconds := tsFloat(value)
	if seconds <= 0 {
		return "-"
	}
	return time.Unix(int64(seconds), 0).Local().Format("2006-01-02 15:04")
}

func conversationSurface(info map[string]any, channelID string) string {
	switch {
	case boolValue(info["is_im"]) || strings.HasPrefix(channelID, "D"):
		return "dm"
	case boolValue(info["is_mpim"]) || strings.HasPrefix(channelID, "G"):
		return "group_dm"
	case boolValue(info["is_private"]) || strings.HasPrefix(channelID, "C") && str(info["is_private"]) == "true":
		return "private_channel"
	default:
		return "channel"
	}
}

func channelName(channel map[string]any, channelID string) string {
	return firstNonEmpty(str(channel["name"]), str(channel["user"]), channelID)
}

func displayUser(user map[string]any, fallback string) string {
	profile := asMap(user["profile"])
	return firstNonEmpty(str(profile["display_name"]), str(profile["real_name"]), str(user["real_name"]), str(user["name"]), fallback)
}

func userEmail(user map[string]any, fallback string) string {
	return firstNonEmpty(str(asMap(user["profile"])["email"]), fallback)
}

func senderInfo(client SlackClient, message map[string]any, userCache map[string]map[string]any) map[string]any {
	userID := str(message["user"])
	if userID == "" {
		userID = str(message["bot_id"])
	}
	if userID == "" {
		return map[string]any{"id": "-", "name": "unknown", "email": "-"}
	}
	if userCache == nil {
		userCache = map[string]map[string]any{}
	}
	user, ok := userCache[userID]
	if !ok {
		loaded, err := getUserInfo(client, userID)
		if err == nil {
			user = loaded
			userCache[userID] = loaded
		}
	}
	return map[string]any{
		"id":    userID,
		"name":  displayUser(user, userID),
		"email": userEmail(user, "-"),
		"label": displayUser(user, userID),
	}
}

func entrySummary(entry MessageEntry) map[string]any {
	return map[string]any{
		"message_id":      messageID(entry.ChannelID, str(entry.Message["ts"])),
		"surface":         entry.Surface,
		"conversation":    entry.Conversation,
		"sender":          str(entry.Sender["name"]),
		"sender_email":    str(entry.Sender["email"]),
		"text":            messageText(entry.Message),
		"date":            formatTS(str(entry.Message["ts"])),
		"unread":          entry.Unread,
		"attachments":     summarizeAttachments(entry.Message),
		"channel_id":      entry.ChannelID,
		"conversation_id": entry.ChannelID,
	}
}

func listEntryFields(entry MessageEntry) []kv {
	summary := entrySummary(entry)
	return []kv{
		{"surface", str(summary["surface"])},
		{"conversation", str(summary["conversation"])},
		{"sender", str(summary["sender"])},
		{"date", str(summary["date"])},
		{"unread", str(summary["unread"])},
		{"message_id", str(summary["message_id"])},
		{"text", compactText(str(summary["text"]))},
		{"attachments", str(summary["attachments"])},
	}
}

func inspectEntryFields(entry MessageEntry) []kv {
	summary := entrySummary(entry)
	text := messageText(entry.Message)
	if text == "" || text == "-" {
		text = str(summary["text"])
	}
	return []kv{
		{"surface", str(summary["surface"])},
		{"conversation", str(summary["conversation"])},
		{"sender", str(summary["sender"])},
		{"date", str(summary["date"])},
		{"unread", str(summary["unread"])},
		{"message_id", str(summary["message_id"])},
		{"text", text},
		{"attachments", str(summary["attachments"])},
	}
}

func compactText(value string) string {
	value = strings.Join(strings.Fields(value), " ")
	if value == "" {
		return "-"
	}
	if len(value) > 240 {
		return value[:237] + "..."
	}
	return value
}

func sortEntriesLatest(entries []MessageEntry) {
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].SortTS > entries[j].SortTS
	})
}
