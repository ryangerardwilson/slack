package app

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

type cachePaths struct {
	DBFile  string
	LogFile string
}

func safePresetSlug(preset string) string {
	re := regexp.MustCompile(`[^A-Za-z0-9._-]+`)
	slug := re.ReplaceAllString(preset, "-")
	slug = strings.Trim(slug, "-")
	if slug == "" {
		return "default"
	}
	return slug
}

func accountInt(account map[string]any, key string, fallback int) int {
	return intValue(account[key], fallback)
}

func eventCachePaths(account map[string]any, preset string) cachePaths {
	slug := safePresetSlug(preset)
	base := stateBaseDir()
	dbPath := firstNonEmpty(str(account["events_cache_db"]), str(account["event_cache_db"]), filepath.Join(base, "events-"+slug+".db"))
	logPath := firstNonEmpty(str(account["events_log_file"]), filepath.Join(base, "events-"+slug+".log"))
	return cachePaths{DBFile: expandPath(dbPath), LogFile: expandPath(logPath)}
}

func eventCacheDBPath(account map[string]any, preset string) string {
	return eventCachePaths(account, preset).DBFile
}

func jsonText(value any) string {
	if value == nil {
		value = map[string]any{}
	}
	data, err := json.Marshal(value)
	if err != nil {
		return "{}"
	}
	return string(data)
}

func jsonMap(value string) map[string]any {
	out := map[string]any{}
	_ = json.Unmarshal([]byte(value), &out)
	return out
}

func eventCacheConnect(path string) (*sql.DB, error) {
	if path == "" {
		return nil, fmt.Errorf("empty event cache path")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	if _, err := db.Exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`); err != nil {
		db.Close()
		return nil, err
	}
	if err := eventCacheInit(db); err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

func eventCacheInit(db *sql.DB) error {
	_, err := db.Exec(`
CREATE TABLE IF NOT EXISTS conversations (
  channel_id TEXT PRIMARY KEY,
  surface TEXT NOT NULL,
  conversation TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  members TEXT NOT NULL,
  user_id TEXT NOT NULL,
  last_read TEXT NOT NULL,
  info_json TEXT NOT NULL,
  latest_ts REAL NOT NULL DEFAULT 0,
  unread_ts REAL NOT NULL DEFAULT 0,
  history_loaded INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  sort_ts REAL NOT NULL,
  user_id TEXT NOT NULL,
  text TEXT NOT NULL,
  unread INTEGER NOT NULL DEFAULT 0,
  sender_json TEXT NOT NULL,
  message_json TEXT NOT NULL,
  event_id TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_ts ON messages(channel_id, sort_ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_sort_ts ON messages(sort_ts DESC);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR REPLACE INTO cache_state(key, value) VALUES('schema_version', ?);
`, strconv.Itoa(eventCacheSchemaVersion))
	return err
}

func eventCacheNow() string {
	return time.Now().Local().Format(time.RFC3339)
}

func eventCacheSetState(db *sql.DB, key, value string) error {
	_, err := db.Exec("INSERT OR REPLACE INTO cache_state(key, value) VALUES(?, ?)", key, value)
	return err
}

func eventCacheGetState(db *sql.DB, key, fallback string) string {
	var value string
	err := db.QueryRow("SELECT value FROM cache_state WHERE key = ?", key).Scan(&value)
	if err != nil {
		return fallback
	}
	return value
}

func eventCacheClaimEvent(db *sql.DB, eventID, channelID, ts string) bool {
	if eventID == "" {
		return true
	}
	_, err := db.Exec("INSERT INTO events(event_id, channel_id, ts, received_at) VALUES(?, ?, ?, ?)", eventID, channelID, ts, eventCacheNow())
	return err == nil
}

func conversationInfoFromEntry(entry MessageEntry) ConversationRow {
	return ConversationRow{
		ChannelID:    firstNonEmpty(entry.ChannelID, entry.DMID),
		Surface:      firstNonEmpty(entry.Surface, "dm"),
		Conversation: firstNonEmpty(entry.Conversation, entry.Email, entry.ChannelID, "-"),
		Name:         firstNonEmpty(entry.Conversation, entry.Email, entry.ChannelID, "-"),
		Email:        firstNonEmpty(entry.Email, "-"),
		Members:      firstNonEmpty(entry.Members, "-"),
		UserID:       firstNonEmpty(entry.UserID, str(entry.Sender["id"]), "-"),
		LastRead:     "0",
		Info:         map[string]any{"last_read": "0"},
	}
}

func eventCacheUpsertConversation(db *sql.DB, row ConversationRow, latestTS float64, unreadTS float64, historyLoaded bool) (float64, error) {
	if row.ChannelID == "" {
		return 0, nil
	}
	var existingInfoJSON, existingLastRead string
	var existingLatest, existingUnread float64
	var existingHistory int
	err := db.QueryRow("SELECT info_json, last_read, latest_ts, unread_ts, history_loaded FROM conversations WHERE channel_id = ?", row.ChannelID).Scan(&existingInfoJSON, &existingLastRead, &existingLatest, &existingUnread, &existingHistory)
	if err != nil && err != sql.ErrNoRows {
		return 0, err
	}
	existingInfo := jsonMap(existingInfoJSON)
	info := map[string]any{}
	for key, value := range existingInfo {
		info[key] = value
	}
	for key, value := range row.Info {
		if str(value) != "" && str(value) != "-" {
			info[key] = value
		}
	}
	lastRead := maxFloat(tsFloat(str(info["last_read"])), tsFloat(existingLastRead), tsFloat(row.LastRead))
	if lastRead > 0 {
		row.LastRead = fmt.Sprintf("%.6f", lastRead)
	} else if row.LastRead == "" {
		row.LastRead = "0"
	}
	info["last_read"] = row.LastRead
	if latestTS < existingLatest {
		latestTS = existingLatest
	}
	if unreadTS < existingUnread {
		unreadTS = existingUnread
	}
	if existingHistory == 1 {
		historyLoaded = true
	}
	if unreadTS > 0 && unreadTS <= lastRead {
		unreadTS = 0
	}
	_, err = db.Exec(`
INSERT OR REPLACE INTO conversations(
  channel_id, surface, conversation, name, email, members, user_id,
  last_read, info_json, latest_ts, unread_ts, history_loaded, updated_at
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		row.ChannelID,
		firstNonEmpty(row.Surface, "dm"),
		firstNonEmpty(row.Conversation, row.Name, row.ChannelID),
		firstNonEmpty(row.Name, row.Conversation, row.ChannelID),
		firstNonEmpty(row.Email, "-"),
		firstNonEmpty(row.Members, "-"),
		firstNonEmpty(row.UserID, "-"),
		row.LastRead,
		jsonText(info),
		latestTS,
		unreadTS,
		boolToInt(historyLoaded),
		eventCacheNow(),
	)
	return lastRead, err
}

func eventCacheUpsertEntry(db *sql.DB, entry MessageEntry, eventID string, historyLoaded bool) (bool, error) {
	channelID := firstNonEmpty(entry.ChannelID, entry.DMID)
	ts := str(entry.Message["ts"])
	if channelID == "" || ts == "" {
		return false, nil
	}
	sortTS := entry.SortTS
	if sortTS == 0 {
		sortTS = tsFloat(ts)
	}
	if !eventCacheClaimEvent(db, eventID, channelID, ts) {
		return false, nil
	}
	unreadTS := 0.0
	if entry.Unread {
		unreadTS = sortTS
	}
	row := conversationInfoFromEntry(entry)
	lastRead, err := eventCacheUpsertConversation(db, row, sortTS, unreadTS, historyLoaded)
	if err != nil {
		return false, err
	}
	unread := entry.Unread && sortTS > lastRead
	_, err = db.Exec(`
INSERT OR REPLACE INTO messages(
  message_id, channel_id, ts, sort_ts, user_id, text, unread,
  sender_json, message_json, event_id, updated_at
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		messageID(channelID, ts),
		channelID,
		ts,
		sortTS,
		firstNonEmpty(str(entry.Message["user"]), str(entry.Sender["id"]), "-"),
		messageText(entry.Message),
		boolToInt(unread),
		jsonText(entry.Sender),
		jsonText(entry.Message),
		eventID,
		eventCacheNow(),
	)
	return err == nil, err
}

func eventCacheStoreEntries(cachePath string, entries []MessageEntry, eventID string, historyLoaded bool) (int, error) {
	if cachePath == "" {
		return 0, nil
	}
	db, err := eventCacheConnect(cachePath)
	if err != nil {
		return 0, err
	}
	defer db.Close()
	tx, err := db.Begin()
	if err != nil {
		return 0, err
	}
	count := 0
	for _, entry := range entries {
		ok, err := eventCacheUpsertEntryTx(tx, entry, eventID, historyLoaded)
		if err != nil {
			tx.Rollback()
			return 0, err
		}
		if ok {
			count++
		}
	}
	return count, tx.Commit()
}

func eventCacheUpsertEntryTx(tx *sql.Tx, entry MessageEntry, eventID string, historyLoaded bool) (bool, error) {
	channelID := firstNonEmpty(entry.ChannelID, entry.DMID)
	ts := str(entry.Message["ts"])
	if channelID == "" || ts == "" {
		return false, nil
	}
	sortTS := entry.SortTS
	if sortTS == 0 {
		sortTS = tsFloat(ts)
	}
	if eventID != "" {
		_, err := tx.Exec("INSERT INTO events(event_id, channel_id, ts, received_at) VALUES(?, ?, ?, ?)", eventID, channelID, ts, eventCacheNow())
		if err != nil {
			return false, nil
		}
	}
	row := conversationInfoFromEntry(entry)
	lastRead, err := eventCacheUpsertConversationTx(tx, row, sortTS, map[bool]float64{true: sortTS, false: 0}[entry.Unread], historyLoaded)
	if err != nil {
		return false, err
	}
	unread := entry.Unread && sortTS > lastRead
	_, err = tx.Exec(`
INSERT OR REPLACE INTO messages(
  message_id, channel_id, ts, sort_ts, user_id, text, unread,
  sender_json, message_json, event_id, updated_at
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		messageID(channelID, ts), channelID, ts, sortTS,
		firstNonEmpty(str(entry.Message["user"]), str(entry.Sender["id"]), "-"),
		messageText(entry.Message), boolToInt(unread), jsonText(entry.Sender), jsonText(entry.Message), eventID, eventCacheNow())
	return err == nil, err
}

func eventCacheUpsertConversationTx(tx *sql.Tx, row ConversationRow, latestTS float64, unreadTS float64, historyLoaded bool) (float64, error) {
	if row.ChannelID == "" {
		return 0, nil
	}
	var existingInfoJSON, existingLastRead string
	var existingLatest, existingUnread float64
	var existingHistory int
	err := tx.QueryRow("SELECT info_json, last_read, latest_ts, unread_ts, history_loaded FROM conversations WHERE channel_id = ?", row.ChannelID).Scan(&existingInfoJSON, &existingLastRead, &existingLatest, &existingUnread, &existingHistory)
	if err != nil && err != sql.ErrNoRows {
		return 0, err
	}
	info := jsonMap(existingInfoJSON)
	for key, value := range row.Info {
		if str(value) != "" && str(value) != "-" {
			info[key] = value
		}
	}
	lastRead := maxFloat(tsFloat(str(info["last_read"])), tsFloat(existingLastRead), tsFloat(row.LastRead))
	if lastRead > 0 {
		row.LastRead = fmt.Sprintf("%.6f", lastRead)
	} else if row.LastRead == "" {
		row.LastRead = "0"
	}
	info["last_read"] = row.LastRead
	if latestTS < existingLatest {
		latestTS = existingLatest
	}
	if unreadTS < existingUnread {
		unreadTS = existingUnread
	}
	if existingHistory == 1 {
		historyLoaded = true
	}
	if unreadTS > 0 && unreadTS <= lastRead {
		unreadTS = 0
	}
	_, err = tx.Exec(`
INSERT OR REPLACE INTO conversations(
  channel_id, surface, conversation, name, email, members, user_id,
  last_read, info_json, latest_ts, unread_ts, history_loaded, updated_at
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		row.ChannelID, firstNonEmpty(row.Surface, "dm"), firstNonEmpty(row.Conversation, row.Name, row.ChannelID), firstNonEmpty(row.Name, row.Conversation, row.ChannelID),
		firstNonEmpty(row.Email, "-"), firstNonEmpty(row.Members, "-"), firstNonEmpty(row.UserID, "-"), row.LastRead, jsonText(info), latestTS, unreadTS, boolToInt(historyLoaded), eventCacheNow())
	return lastRead, err
}

func eventCacheStoreConversationRow(cachePath string, row ConversationRow, historyLoaded bool) (int, error) {
	if cachePath == "" || row.ChannelID == "" {
		return 0, nil
	}
	db, err := eventCacheConnect(cachePath)
	if err != nil {
		return 0, err
	}
	defer db.Close()
	latest := tsFloat(row.LatestTS)
	unreadTS := 0.0
	if row.Unread > 0 {
		unreadTS = latest
	}
	if _, err := eventCacheUpsertConversation(db, row, latest, unreadTS, historyLoaded || row.HistoryLoaded); err != nil {
		return 0, err
	}
	count := 0
	for _, entry := range row.Messages {
		if entry.ChannelID == "" {
			entry.ChannelID = row.ChannelID
			entry.DMID = row.ChannelID
		}
		ok, err := eventCacheUpsertEntry(db, entry, "", historyLoaded)
		if err != nil {
			return 0, err
		}
		if ok {
			count++
		}
	}
	return count, nil
}

func eventCacheLoadEntries(cachePath, selfUserID string, limit int, channelID string) ([]MessageEntry, error) {
	if cachePath == "" {
		return nil, nil
	}
	if _, err := os.Stat(cachePath); err != nil {
		return nil, nil
	}
	db, err := eventCacheConnect(cachePath)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	query := `
SELECT messages.channel_id, messages.ts, messages.sort_ts, messages.unread,
       messages.sender_json, messages.message_json, conversations.info_json,
       conversations.surface, conversations.conversation, conversations.name,
       conversations.email, conversations.members, conversations.user_id,
       conversations.last_read
FROM messages
JOIN conversations ON conversations.channel_id = messages.channel_id`
	var args []any
	if channelID != "" {
		query += " WHERE messages.channel_id = ?"
		args = append(args, channelID)
	}
	query += " ORDER BY messages.sort_ts DESC LIMIT ?"
	args = append(args, maxInt(1, limit))
	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var entries []MessageEntry
	for rows.Next() {
		var channelID, ts, senderJSON, messageJSON, infoJSON, surface, conversation, name, email, members, userID, lastRead string
		var sortTS float64
		var unreadInt int
		if err := rows.Scan(&channelID, &ts, &sortTS, &unreadInt, &senderJSON, &messageJSON, &infoJSON, &surface, &conversation, &name, &email, &members, &userID, &lastRead); err != nil {
			return nil, err
		}
		message := jsonMap(messageJSON)
		sender := jsonMap(senderJSON)
		unread := unreadInt == 1
		if sortTS <= tsFloat(lastRead) || (selfUserID != "" && str(message["user"]) == selfUserID) {
			unread = false
		}
		_ = infoJSON
		entries = append(entries, MessageEntry{
			SortTS:       sortTS,
			Email:        firstNonEmpty(email, "-"),
			DMID:         channelID,
			ChannelID:    channelID,
			Surface:      firstNonEmpty(surface, "dm"),
			Conversation: firstNonEmpty(conversation, name, channelID),
			UserID:       firstNonEmpty(userID, "-"),
			Members:      firstNonEmpty(members, "-"),
			Message:      message,
			Sender:       sender,
			Unread:       unread,
		})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].SortTS < entries[j].SortTS })
	return entries, rows.Err()
}

func eventCacheLoadConversationRows(cachePath, selfUserID string, limit int) ([]ConversationRow, error) {
	entries, err := eventCacheLoadEntries(cachePath, selfUserID, maxInt(500, limit*5), "")
	if err != nil || len(entries) == 0 {
		return nil, err
	}
	rows := conversationRowsFromEntries(entries)
	var filtered []ConversationRow
	history := eventCacheHistoryLoadedMap(cachePath)
	for _, row := range rows {
		if row.Surface == "dm" || row.Surface == "group_dm" {
			row.HistoryLoaded = history[row.ChannelID]
			filtered = append(filtered, row)
		}
	}
	if len(filtered) > limit {
		filtered = filtered[:limit]
	}
	return filtered, nil
}

func eventCacheHistoryLoadedMap(cachePath string) map[string]bool {
	out := map[string]bool{}
	if _, err := os.Stat(cachePath); err != nil {
		return out
	}
	db, err := eventCacheConnect(cachePath)
	if err != nil {
		return out
	}
	defer db.Close()
	rows, err := db.Query("SELECT channel_id, history_loaded FROM conversations")
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var channelID string
		var loaded int
		if rows.Scan(&channelID, &loaded) == nil {
			out[channelID] = loaded == 1
		}
	}
	return out
}

func eventCacheLoadChannelEntries(cachePath, channelID, selfUserID string, limit int) ([]MessageEntry, error) {
	return eventCacheLoadEntries(cachePath, selfUserID, limit, channelID)
}

func eventCacheMarkRead(cachePath, channelID, latestTS string) error {
	if cachePath == "" || channelID == "" || latestTS == "" {
		return nil
	}
	db, err := eventCacheConnect(cachePath)
	if err != nil {
		return err
	}
	defer db.Close()
	var infoJSON string
	_ = db.QueryRow("SELECT info_json FROM conversations WHERE channel_id = ?", channelID).Scan(&infoJSON)
	info := jsonMap(infoJSON)
	info["last_read"] = latestTS
	_, err = db.Exec("UPDATE conversations SET unread_ts = 0, last_read = ?, info_json = ?, updated_at = ? WHERE channel_id = ?", latestTS, jsonText(info), eventCacheNow(), channelID)
	if err != nil {
		return err
	}
	_, err = db.Exec("UPDATE messages SET unread = 0, updated_at = ? WHERE channel_id = ? AND sort_ts <= ?", eventCacheNow(), channelID, tsFloat(latestTS))
	return err
}

func eventCacheSearchEntries(cachePath string, contacts Contacts, limit int, filterMode, selfUserID, label, senderFilter, containsFilter, timeLimit string) ([]MessageEntry, error) {
	entries, err := eventCacheLoadEntries(cachePath, selfUserID, maxInt(200, limit*20), "")
	if err != nil || len(entries) == 0 {
		return entries, err
	}
	cutoff, hasCutoff := startTS(timeLimit)
	var selected []MessageEntry
	sortEntriesLatest(entries)
	for _, entry := range entries {
		if !cacheLabelMatches(entry, contacts, label) {
			continue
		}
		if !entryPassesFilters(entry, filterMode, senderFilter, containsFilter, cutoff, hasCutoff) {
			continue
		}
		selected = append(selected, entry)
		if len(selected) >= limit {
			break
		}
	}
	sort.Slice(selected, func(i, j int) bool { return selected[i].SortTS < selected[j].SortTS })
	return selected, nil
}

func cacheLabelMatches(entry MessageEntry, contacts Contacts, label string) bool {
	if label == "" {
		return true
	}
	target, ok := contacts[label]
	if !ok {
		return false
	}
	haystack := strings.ToLower(strings.Join([]string{
		label, target, entry.Conversation, entry.Email, entry.UserID,
		str(entry.Sender["id"]), str(entry.Sender["name"]), str(entry.Sender["email"]), str(entry.Sender["label"]),
	}, " "))
	target = strings.ToLower(target)
	return target != "" && strings.Contains(haystack, target) || strings.Contains(haystack, strings.ToLower(label))
}

func entryPassesFilters(entry MessageEntry, filterMode, senderFilter, containsFilter string, cutoff float64, hasCutoff bool) bool {
	if filterMode == "unread" && !entry.Unread {
		return false
	}
	if filterMode == "read" && entry.Unread {
		return false
	}
	if senderFilter != "" {
		haystack := strings.ToLower(strings.Join([]string{str(entry.Sender["name"]), str(entry.Sender["email"]), str(entry.Sender["label"]), str(entry.Sender["id"])}, " "))
		if !strings.Contains(haystack, strings.ToLower(senderFilter)) {
			return false
		}
	}
	if containsFilter != "" && !strings.Contains(strings.ToLower(messageText(entry.Message)), strings.ToLower(containsFilter)) {
		return false
	}
	if hasCutoff && entry.SortTS < cutoff {
		return false
	}
	return true
}

func startTS(value string) (float64, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0, false
	}
	now := time.Now()
	if match := relativeTimeRE.FindStringSubmatch(value); len(match) == 3 {
		amount, _ := strconv.Atoi(match[1])
		switch strings.ToLower(match[2]) {
		case "d":
			return float64(now.AddDate(0, 0, -amount).Unix()), true
		case "w":
			return float64(now.AddDate(0, 0, -amount*7).Unix()), true
		case "m":
			return float64(now.AddDate(0, -amount, 0).Unix()), true
		case "y":
			return float64(now.AddDate(-amount, 0, 0).Unix()), true
		}
	}
	if strings.Contains(value, "..") {
		value = strings.SplitN(value, "..", 2)[0]
	}
	layouts := []string{"2006-01-02", "2006-01"}
	for _, layout := range layouts {
		if parsed, err := time.ParseInLocation(layout, value, time.Local); err == nil {
			return float64(parsed.Unix()), true
		}
	}
	if match := namedMonthRE.FindStringSubmatch(value); len(match) == 3 {
		month := monthNumber(match[1])
		year, _ := strconv.Atoi(match[2])
		if month > 0 {
			return float64(time.Date(year, time.Month(month), 1, 0, 0, 0, 0, time.Local).Unix()), true
		}
	}
	_ = isoDateRE
	_ = isoMonthRE
	return 0, false
}

func monthNumber(value string) int {
	months := map[string]int{"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
	return months[strings.ToLower(value)]
}

func boolToInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func maxFloat(values ...float64) float64 {
	out := 0.0
	for _, value := range values {
		if value > out {
			out = value
		}
	}
	return out
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
