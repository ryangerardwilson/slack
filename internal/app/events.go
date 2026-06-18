package app

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"nhooyr.io/websocket"
)

func (rt *Runtime) dispatchEvents(account Account, preset string, args Args) error {
	switch args.EventsAction {
	case "help":
		fmt.Fprintln(rt.Stdout, `Usage:
  slack <preset> events sync
  slack <preset> events once
  slack <preset> events service
  slack <preset> events timer install
  slack <preset> events timer disable
  slack <preset> events timer status
  slack <preset> events logs [lines]
  slack <preset> events status
  slack <preset> events reset cache`)
		return nil
	case "sync":
		_, err := rt.eventsSyncOnce(account, preset, false)
		return err
	case "once":
		processed, err := rt.eventsSocketLoop(account, preset, true)
		if err != nil {
			return err
		}
		fmt.Fprintf(rt.Stdout, "events_once processed=%d\n", processed)
		return nil
	case "service":
		return rt.eventsService(account, preset)
	case "ti":
		return rt.eventsInstallService(preset)
	case "td":
		return rt.eventsDisableService(preset)
	case "st":
		return rt.systemctlUser("status", eventsUnitName(preset)+".service", "--no-pager")
	case "logs":
		return rt.journalctlUser(eventsUnitName(preset)+".service", args.EventsLines)
	case "status":
		return rt.eventsStatus(account, preset)
	case "reset-cache":
		return rt.eventsResetCache(account, preset)
	default:
		return UsageError{Message: "Use: slack <preset> events sync|once|service|status|logs|reset cache|timer install|timer disable|timer status"}
	}
}

func (rt *Runtime) eventsSyncOnce(account Account, preset string, quiet bool) (int, error) {
	token, err := resolveListToken(account)
	if err != nil {
		return 0, err
	}
	client := rt.slackClient(token)
	auth, err := client.AuthTest()
	if err != nil {
		return 0, err
	}
	selfUserID := str(auth["user_id"])
	if selfUserID == "" {
		return 0, UsageError{Message: "Unable to determine the current Slack user."}
	}
	cachePath := eventCacheDBPath(account, preset)
	rows, err := rt.loadRecentConversations(client, selfUserID, "", 100, false, conversationTypesMember)
	if err != nil {
		return 0, err
	}
	stored := 0
	for _, row := range rows {
		_, _ = eventCacheStoreConversationRow(cachePath, row, row.HistoryLoaded)
	}
	limit := accountInt(account, "events_sync_conversation_limit", eventSyncConversationLimit)
	for index, row := range rows {
		if index >= limit {
			break
		}
		entries, err := rt.loadConversationMessages(client, row, selfUserID, 100, cachePath)
		if err != nil {
			continue
		}
		row.Messages = entries
		row.HistoryLoaded = true
		count, _ := eventCacheStoreConversationRow(cachePath, row, true)
		stored += count
	}
	db, err := eventCacheConnect(cachePath)
	if err == nil {
		_ = eventCacheSetState(db, "last_sync_at", eventCacheNow())
		_ = eventCacheSetState(db, "last_sync_conversations", fmt.Sprintf("%d", len(rows)))
		_ = eventCacheSetState(db, "last_sync_messages", fmt.Sprintf("%d", stored))
		db.Close()
	}
	if !quiet {
		fmt.Fprintf(rt.Stdout, "events_sync conversations=%d messages=%d cache=%s\n", len(rows), stored, cachePath)
	}
	return stored, nil
}

func (rt *Runtime) openSocketModeConnection(appToken string) (*websocket.Conn, error) {
	client := rt.slackClient(appToken)
	data, err := client.Request("apps.connections.open", map[string]string{}, true, http.MethodPost, false)
	if err != nil {
		return nil, err
	}
	socketURL := str(data["url"])
	if socketURL == "" {
		return nil, fmt.Errorf("Slack did not return a Socket Mode URL.")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, socketURL, nil)
	return conn, err
}

func (rt *Runtime) eventsSocketLoop(account Account, preset string, once bool) (int, error) {
	appToken, err := resolveAppToken(account)
	if err != nil {
		return 0, err
	}
	token, err := resolveListToken(account)
	if err != nil {
		return 0, err
	}
	client := rt.slackClient(token)
	auth, err := client.AuthTest()
	if err != nil {
		return 0, err
	}
	selfUserID := str(auth["user_id"])
	if selfUserID == "" {
		return 0, UsageError{Message: "Unable to determine the current Slack user."}
	}
	conn, err := rt.openSocketModeConnection(appToken)
	if err != nil {
		return 0, err
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")
	timeout := time.Duration(accountInt(account, "events_socket_timeout_seconds", eventSocketTimeoutSeconds)) * time.Second
	processed := 0
	for {
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		_, data, err := conn.Read(ctx)
		cancel()
		if err != nil {
			if once {
				rt.eventsLog(account, preset, "once timeout/no event: "+err.Error())
				return processed, nil
			}
			return processed, err
		}
		var envelope map[string]any
		if json.Unmarshal(data, &envelope) != nil {
			continue
		}
		envelopeType := str(envelope["type"])
		if envelopeType == "hello" {
			rt.eventsLog(account, preset, "socket connected")
			continue
		}
		if envelopeType == "disconnect" {
			rt.eventsLog(account, preset, "socket disconnect: "+firstNonEmpty(str(envelope["reason"]), "-"))
			return processed, nil
		}
		if envelopeID := str(envelope["envelope_id"]); envelopeID != "" {
			ack, _ := json.Marshal(map[string]any{"envelope_id": envelopeID})
			_ = conn.Write(context.Background(), websocket.MessageText, ack)
		}
		if envelopeType != "events_api" {
			continue
		}
		payload := asMap(envelope["payload"])
		if rt.eventCacheStoreSocketPayload(account, preset, payload, client, selfUserID) {
			processed++
		}
		if once && processed > 0 {
			return processed, nil
		}
	}
}

func (rt *Runtime) eventCacheStoreSocketPayload(account Account, preset string, payload map[string]any, client SlackClient, selfUserID string) bool {
	event := asMap(payload["event"])
	if len(event) == 0 || str(event["type"]) != "message" || str(event["subtype"]) != "" {
		return false
	}
	channelID := str(event["channel"])
	ts := str(event["ts"])
	if channelID == "" || ts == "" {
		return false
	}
	channelInfo := map[string]any{"id": channelID}
	if info, err := client.Request("conversations.info", map[string]string{"channel": channelID, "include_num_members": "true"}, false, http.MethodGet, true); err == nil && info["ok"] == true {
		channelInfo = asMap(info["channel"])
	}
	surface := conversationSurface(channelInfo, channelID)
	if surface != "dm" && surface != "group_dm" {
		return false
	}
	sender := senderInfo(client, event, nil)
	row := ConversationRow{
		ChannelID:    channelID,
		Surface:      surface,
		Conversation: firstNonEmpty(channelName(channelInfo, channelID), channelID),
		Name:         channelName(channelInfo, channelID),
		Email:        firstNonEmpty(str(sender["email"]), "-"),
		Members:      firstNonEmpty(str(channelInfo["num_members"]), "-"),
		UserID:       firstNonEmpty(str(channelInfo["user"]), str(sender["id"]), "-"),
		LatestTS:     ts,
		LastRead:     str(channelInfo["last_read"]),
		Unread:       boolToInt(str(event["user"]) != selfUserID),
		Info:         channelInfo,
	}
	entry := MessageEntry{
		SortTS:       tsFloat(ts),
		Email:        row.Email,
		DMID:         channelID,
		ChannelID:    channelID,
		Surface:      surface,
		Conversation: row.Conversation,
		UserID:       row.UserID,
		Members:      row.Members,
		Message:      event,
		Sender:       sender,
		Unread:       str(event["user"]) != selfUserID,
	}
	cachePath := eventCacheDBPath(account, preset)
	_, _ = eventCacheStoreConversationRow(cachePath, row, false)
	count, err := eventCacheStoreEntries(cachePath, []MessageEntry{entry}, str(payload["event_id"]), false)
	if err == nil && count > 0 {
		db, err := eventCacheConnect(cachePath)
		if err == nil {
			_ = eventCacheSetState(db, "processed_events", fmt.Sprintf("%d", intValue(eventCacheGetState(db, "processed_events", "0"), 0)+1))
			_ = eventCacheSetState(db, "last_event_at", eventCacheNow())
			_ = eventCacheSetState(db, "last_channel", channelID)
			_ = eventCacheSetState(db, "last_message_ts", ts)
			db.Close()
		}
	}
	return err == nil && count > 0
}

func (rt *Runtime) eventsService(account Account, preset string) error {
	rt.eventsLog(account, preset, "service started")
	syncInterval := time.Duration(maxInt(60, accountInt(account, "events_sync_seconds", eventSyncSeconds))) * time.Second
	go func() {
		for {
			if _, err := rt.eventsSyncOnce(account, preset, true); err != nil {
				rt.eventsLog(account, preset, "sync error: "+err.Error())
			} else {
				rt.eventsLog(account, preset, "sync complete")
			}
			time.Sleep(syncInterval)
		}
	}()
	for {
		if _, err := rt.eventsSocketLoop(account, preset, false); err != nil {
			rt.eventsLog(account, preset, "service error: "+err.Error())
		}
		time.Sleep(5 * time.Second)
	}
}

func eventsUnitName(preset string) string {
	return "slack-events-" + safePresetSlug(preset)
}

func eventsUnitPath(preset string) string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "systemd", "user", eventsUnitName(preset)+".service")
}

func writeEventsUnit(preset string) (string, error) {
	unitPath := eventsUnitPath(preset)
	if err := os.MkdirAll(filepath.Dir(unitPath), 0o755); err != nil {
		return "", err
	}
	body := strings.Join([]string{
		"[Unit]",
		fmt.Sprintf("Description=Slack preset %s realtime DM/GDM event cache", preset),
		"After=network-online.target",
		"",
		"[Service]",
		"Type=simple",
		fmt.Sprintf("ExecStart=%%h/.local/bin/slack %s events service", preset),
		"Restart=always",
		"RestartSec=5",
		"Nice=5",
		"",
		"[Install]",
		"WantedBy=default.target",
		"",
	}, "\n")
	return unitPath, os.WriteFile(unitPath, []byte(body), 0o644)
}

func (rt *Runtime) eventsInstallService(preset string) error {
	if _, err := writeEventsUnit(preset); err != nil {
		return err
	}
	unit := eventsUnitName(preset) + ".service"
	if err := rt.systemctlUser("daemon-reload"); err != nil {
		return err
	}
	if err := rt.systemctlUser("enable", "--now", unit); err != nil {
		return err
	}
	if err := rt.systemctlUser("restart", unit); err != nil {
		return err
	}
	fmt.Fprintf(rt.Stdout, "service enabled: %s\n", unit)
	return nil
}

func (rt *Runtime) eventsDisableService(preset string) error {
	_, _ = writeEventsUnit(preset)
	unit := eventsUnitName(preset) + ".service"
	_ = rt.systemctlUser("disable", "--now", unit)
	if err := rt.systemctlUser("daemon-reload"); err != nil {
		return err
	}
	fmt.Fprintf(rt.Stdout, "service disabled: %s\n", unit)
	return nil
}

func (rt *Runtime) eventsStatus(account Account, preset string) error {
	paths := eventCachePaths(account, preset)
	state := map[string]any{
		"cache":          paths.DBFile,
		"log":            paths.LogFile,
		"exists":         fileExists(paths.DBFile),
		"has_app_token":  hasToken(account, "app") || readTokenFile(defaultAppTokenFile) != "",
		"has_user_token": hasToken(account, "user") || readTokenFile(defaultUserTokenFile) != "",
	}
	if fileExists(paths.DBFile) {
		db, err := eventCacheConnect(paths.DBFile)
		if err == nil {
			state["conversations"] = countRows(db, "conversations")
			state["messages"] = countRows(db, "messages")
			state["processed_events"] = eventCacheGetState(db, "processed_events", "0")
			state["last_event_at"] = eventCacheGetState(db, "last_event_at", "")
			state["last_sync_at"] = eventCacheGetState(db, "last_sync_at", "")
			state["last_channel"] = eventCacheGetState(db, "last_channel", "")
			state["last_message_ts"] = eventCacheGetState(db, "last_message_ts", "")
			db.Close()
		}
	}
	return rt.printJSON(state)
}

func countRows(db *sql.DB, table string) int {
	var count int
	_ = db.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&count)
	return count
}

func (rt *Runtime) eventsResetCache(account Account, preset string) error {
	paths := eventCachePaths(account, preset)
	for _, path := range []string{paths.DBFile, paths.DBFile + "-wal", paths.DBFile + "-shm"} {
		_ = os.Remove(path)
	}
	rt.eventsLog(account, preset, "cache reset")
	fmt.Fprintf(rt.Stdout, "cache reset: %s\n", paths.DBFile)
	return nil
}

func (rt *Runtime) eventsLog(account Account, preset string, message string) {
	paths := eventCachePaths(account, preset)
	_ = os.MkdirAll(filepath.Dir(paths.LogFile), 0o755)
	f, err := os.OpenFile(paths.LogFile, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "%s %s\n", time.Now().Local().Format(time.RFC3339), message)
}

func (rt *Runtime) systemctlUser(args ...string) error {
	cmd := exec.Command("systemctl", append([]string{"--user"}, args...)...)
	cmd.Stdout = rt.Stdout
	cmd.Stderr = rt.Stderr
	return cmd.Run()
}

func (rt *Runtime) journalctlUser(unit string, lines int) error {
	cmd := exec.Command("journalctl", "--user", "-u", unit, "-n", fmt.Sprintf("%d", lines), "--no-pager")
	cmd.Stdout = rt.Stdout
	cmd.Stderr = rt.Stderr
	return cmd.Run()
}
