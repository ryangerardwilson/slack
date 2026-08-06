package app

import (
	"database/sql"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func (rt *Runtime) dispatchEvents(account Account, preset string, args Args) error {
	switch args.EventsAction {
	case "help":
		fmt.Fprintln(rt.Stdout, `Usage:
  slack <preset> events sync
  slack <preset> events status
  slack <preset> events reset cache`)
		return nil
	case "sync":
		_, err := rt.eventsSyncOnce(account, preset, false)
		return err
	case "status":
		return rt.eventsStatus(account, preset)
	case "reset-cache":
		return rt.eventsResetCache(account, preset)
	case "timer-disable":
		return rt.eventsDisableLegacyService(preset)
	default:
		return UsageError{Message: "Use: slack <preset> events sync|status|reset cache"}
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

func (rt *Runtime) eventsStatus(account Account, preset string) error {
	paths := eventCachePaths(account, preset)
	state := map[string]any{
		"cache":          paths.DBFile,
		"exists":         fileExists(paths.DBFile),
		"has_user_token": hasToken(account, "user") || readTokenFile(defaultUserTokenFile) != "",
	}
	if fileExists(paths.DBFile) {
		db, err := eventCacheConnect(paths.DBFile)
		if err == nil {
			state["conversations"] = countRows(db, "conversations")
			state["messages"] = countRows(db, "messages")
			state["last_sync_at"] = eventCacheGetState(db, "last_sync_at", "")
			state["last_sync_conversations"] = eventCacheGetState(db, "last_sync_conversations", "0")
			state["last_sync_messages"] = eventCacheGetState(db, "last_sync_messages", "0")
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
	fmt.Fprintf(rt.Stdout, "cache reset: %s\n", paths.DBFile)
	return nil
}

func eventsUnitName(preset string) string {
	return "slack-events-" + safePresetSlug(preset)
}

func eventsUnitPath(preset string) string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "systemd", "user", eventsUnitName(preset)+".service")
}

func (rt *Runtime) eventsDisableLegacyService(preset string) error {
	unit := eventsUnitName(preset) + ".service"
	_ = rt.systemctlUser("disable", "--now", unit)
	_ = os.Remove(eventsUnitPath(preset))
	if err := rt.systemctlUser("daemon-reload"); err != nil {
		return err
	}
	fmt.Fprintf(rt.Stdout, "legacy event service disabled: %s\n", unit)
	return nil
}

func (rt *Runtime) systemctlUser(args ...string) error {
	cmd := exec.Command("systemctl", append([]string{"--user"}, args...)...)
	cmd.Stdout = rt.Stdout
	cmd.Stderr = rt.Stderr
	return cmd.Run()
}
