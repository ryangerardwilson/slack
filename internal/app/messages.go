package app

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func (rt *Runtime) listMessages(contacts Contacts, client SlackClient, args Args, selfUserID, cachePath string) error {
	if args.ListLabel != "" {
		if _, ok := contacts[args.ListLabel]; !ok {
			return UsageError{Message: "Unknown contact label: " + args.ListLabel}
		}
	}
	entries, err := eventCacheSearchEntries(cachePath, contacts, args.ListLimit, args.ListFilter, selfUserID, args.ListLabel, args.ListFrom, args.ListContains, args.ListTimeLimit)
	if err != nil {
		return err
	}
	if len(entries) == 0 {
		entries, err = rt.searchMessagesLive(contacts, client, args, selfUserID)
		if err != nil {
			return err
		}
	}
	if len(entries) == 0 {
		fmt.Fprintf(rt.Stdout, "No %s messages found.\n", args.ListFilter)
		return nil
	}
	sortEntriesLatest(entries)
	if len(entries) > args.ListLimit {
		entries = entries[:args.ListLimit]
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].SortTS < entries[j].SortTS })
	if args.OpenMode {
		return rt.printOpenEntriesAndMark(entries, client, cachePath)
	}
	if args.OutputJSON {
		summaries := make([]map[string]any, 0, len(entries))
		for _, entry := range entries {
			summaries = append(summaries, entrySummary(entry))
		}
		return rt.printJSON(map[string]any{"messages": summaries})
	}
	var rows [][]kv
	for _, entry := range entries {
		rows = append(rows, listEntryFields(entry))
	}
	rt.printSections(rows)
	return nil
}

func (rt *Runtime) searchMessagesLive(contacts Contacts, client SlackClient, args Args, selfUserID string) ([]MessageEntry, error) {
	if tokenKind(client.Token) == "user" {
		entries, err := rt.searchMessagesAPI(contacts, client, args, selfUserID)
		if err == nil && entries != nil {
			return entries, nil
		}
	}
	return rt.scanConversations(contacts, client, args, selfUserID)
}

func (rt *Runtime) searchMessagesAPI(contacts Contacts, client SlackClient, args Args, selfUserID string) ([]MessageEntry, error) {
	query := buildSearchQuery(args, contacts)
	data, err := client.Request("search.messages", map[string]string{
		"query":    query,
		"count":    fmt.Sprintf("%d", maxInt(args.ListLimit*4, 20)),
		"sort":     "timestamp",
		"sort_dir": "desc",
	}, false, http.MethodGet, true)
	if err != nil || data["ok"] != true {
		return nil, err
	}
	cutoff, hasCutoff := startTS(args.ListTimeLimit)
	userCache := map[string]map[string]any{}
	var entries []MessageEntry
	matches := asMap(data["messages"])["matches"]
	for _, raw := range asList(matches) {
		match := asMap(raw)
		channel := asMap(match["channel"])
		channelID := firstNonEmpty(str(channel["id"]), str(match["channel_id"]))
		if channelID == "" {
			continue
		}
		ts := str(match["ts"])
		if ts == "" {
			continue
		}
		message := map[string]any{
			"ts":   ts,
			"text": str(match["text"]),
			"user": str(asMap(match["user"])["id"]),
		}
		if str(message["user"]) == "" {
			message["user"] = str(match["user"])
		}
		sender := senderInfo(client, message, userCache)
		entry := MessageEntry{
			SortTS:       tsFloat(ts),
			Email:        firstNonEmpty(str(sender["email"]), "-"),
			DMID:         channelID,
			ChannelID:    channelID,
			Surface:      firstNonEmpty(str(channel["type"]), conversationSurface(channel, channelID)),
			Conversation: firstNonEmpty(str(channel["name"]), channelID),
			UserID:       str(sender["id"]),
			Members:      firstNonEmpty(str(channel["num_members"]), "-"),
			Message:      message,
			Sender:       sender,
			Unread:       false,
		}
		if !entryPassesFilters(entry, args.ListFilter, args.ListFrom, args.ListContains, cutoff, hasCutoff) {
			continue
		}
		if args.ListLabel != "" && !cacheLabelMatches(entry, contacts, args.ListLabel) {
			continue
		}
		if str(message["user"]) == selfUserID {
			entry.Unread = false
		}
		entries = append(entries, entry)
	}
	return entries, nil
}

func buildSearchQuery(args Args, contacts Contacts) string {
	var terms []string
	if args.ListContains != "" {
		terms = append(terms, quoteSearch(args.ListContains))
	}
	if args.ListLabel != "" {
		target := contacts[args.ListLabel]
		if strings.Contains(target, "@") {
			terms = append(terms, quoteSearch(target))
		} else if target != "" {
			terms = append(terms, target)
		}
	}
	if args.ListFrom != "" {
		terms = append(terms, "from:"+quoteSearch(args.ListFrom))
	}
	if args.ListTimeLimit != "" {
		if cutoff, ok := startTS(args.ListTimeLimit); ok {
			terms = append(terms, "after:"+time.Unix(int64(cutoff), 0).Format("2006-01-02"))
		}
	}
	if len(terms) == 0 {
		terms = append(terms, "in:im OR in:mpim")
	}
	return strings.Join(terms, " ")
}

func quoteSearch(value string) string {
	value = strings.ReplaceAll(value, `"`, `\"`)
	if strings.ContainsAny(value, " \t") {
		return `"` + value + `"`
	}
	return value
}

func (rt *Runtime) scanConversations(contacts Contacts, client SlackClient, args Args, selfUserID string) ([]MessageEntry, error) {
	rows, err := rt.loadRecentConversations(client, selfUserID, "", maxInt(args.ListLimit*4, 20), false)
	if err != nil {
		return nil, err
	}
	cutoff, hasCutoff := startTS(args.ListTimeLimit)
	var entries []MessageEntry
	for _, row := range rows {
		history, err := rt.loadConversationMessages(client, row, selfUserID, maxInt(args.ListLimit*4, 20), "")
		if err != nil {
			continue
		}
		for _, entry := range history {
			if args.ListLabel != "" && !cacheLabelMatches(entry, contacts, args.ListLabel) {
				continue
			}
			if !entryPassesFilters(entry, args.ListFilter, args.ListFrom, args.ListContains, cutoff, hasCutoff) {
				continue
			}
			entries = append(entries, entry)
		}
	}
	return entries, nil
}

func (rt *Runtime) loadRecentConversations(client SlackClient, selfUserID, cachePath string, limit int, useCache bool) ([]ConversationRow, error) {
	if useCache && cachePath != "" {
		rows, err := eventCacheLoadConversationRows(cachePath, selfUserID, limit)
		if err == nil && len(rows) > 0 {
			return rows, nil
		}
	}
	channels, err := listAPI(client, "users.conversations", map[string]string{
		"types":            "im,mpim",
		"exclude_archived": "true",
		"limit":            "200",
	}, "channels")
	if err != nil {
		return nil, err
	}
	userCache := map[string]map[string]any{}
	var rows []ConversationRow
	for _, channel := range channels {
		channelID := str(channel["id"])
		if channelID == "" {
			continue
		}
		info := channel
		surface := conversationSurface(info, channelID)
		label := channelName(info, channelID)
		userID := str(info["user"])
		email := "-"
		if userID != "" && userID != selfUserID {
			user, err := getUserInfo(client, userID)
			if err == nil {
				userCache[userID] = user
				label = displayUser(user, label)
				email = userEmail(user, "-")
			}
		}
		latestTS := extractTS(info)
		unread := maxInt(intValue(info["unread_count_display"], 0), intValue(info["unread_count"], 0))
		if unread == 0 && boolValue(info["has_unreads"]) {
			unread = 1
		}
		rows = append(rows, ConversationRow{
			ChannelID:    channelID,
			Surface:      surface,
			Conversation: label,
			Name:         label,
			Email:        email,
			Members:      firstNonEmpty(str(info["num_members"]), "-"),
			UserID:       firstNonEmpty(userID, "-"),
			LatestTS:     latestTS,
			LastRead:     str(info["last_read"]),
			Unread:       unread,
			Info:         info,
		})
	}
	sort.Slice(rows, func(i, j int) bool { return tsFloat(rows[i].LatestTS) > tsFloat(rows[j].LatestTS) })
	if len(rows) > limit {
		rows = rows[:limit]
	}
	return rows, nil
}

func (rt *Runtime) loadConversationMessages(client SlackClient, row ConversationRow, selfUserID string, limit int, cachePath string) ([]MessageEntry, error) {
	if cachePath != "" && row.HistoryLoaded {
		entries, err := eventCacheLoadChannelEntries(cachePath, row.ChannelID, selfUserID, limit)
		if err == nil && len(entries) > 0 {
			return entries, nil
		}
	}
	data, err := client.Request("conversations.history", map[string]string{
		"channel": row.ChannelID,
		"limit":   fmt.Sprintf("%d", maxInt(1, limit)),
	}, false, http.MethodGet, false)
	if err != nil {
		return nil, err
	}
	userCache := map[string]map[string]any{}
	var entries []MessageEntry
	for _, raw := range asList(data["messages"]) {
		message := asMap(raw)
		ts := str(message["ts"])
		if ts == "" {
			continue
		}
		sender := senderInfo(client, message, userCache)
		unread := row.Unread > 0 && tsFloat(ts) > tsFloat(row.LastRead) && str(message["user"]) != selfUserID
		entries = append(entries, MessageEntry{
			SortTS:       tsFloat(ts),
			Email:        firstNonEmpty(str(sender["email"]), row.Email, "-"),
			DMID:         row.ChannelID,
			ChannelID:    row.ChannelID,
			Surface:      row.Surface,
			Conversation: row.Conversation,
			UserID:       firstNonEmpty(row.UserID, str(sender["id"]), "-"),
			Members:      firstNonEmpty(row.Members, "-"),
			Message:      message,
			Sender:       sender,
			Unread:       unread,
		})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].SortTS < entries[j].SortTS })
	if cachePath != "" {
		row.Messages = entries
		row.HistoryLoaded = true
		_, _ = eventCacheStoreConversationRow(cachePath, row, true)
	}
	return entries, nil
}

func conversationRowsFromEntries(entries []MessageEntry) []ConversationRow {
	byChannel := map[string]ConversationRow{}
	for _, entry := range entries {
		row := byChannel[entry.ChannelID]
		if row.ChannelID == "" {
			row = ConversationRow{
				ChannelID:    entry.ChannelID,
				Surface:      entry.Surface,
				Conversation: entry.Conversation,
				Name:         entry.Conversation,
				Email:        entry.Email,
				Members:      entry.Members,
				UserID:       entry.UserID,
				Info:         map[string]any{"channel_id": entry.ChannelID, "surface": entry.Surface, "conversation": entry.Conversation},
			}
		}
		if entry.SortTS > tsFloat(row.LatestTS) {
			row.LatestTS = str(entry.Message["ts"])
		}
		if entry.Unread {
			row.Unread++
		}
		row.Messages = append(row.Messages, entry)
		byChannel[entry.ChannelID] = row
	}
	rows := make([]ConversationRow, 0, len(byChannel))
	for _, row := range byChannel {
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return tsFloat(rows[i].LatestTS) > tsFloat(rows[j].LatestTS) })
	return rows
}

func (rt *Runtime) printOpenEntriesAndMark(entries []MessageEntry, client SlackClient, cachePath string) error {
	latest := map[string]string{}
	for _, entry := range entries {
		rt.printMessageOpen(entry)
		ts := str(entry.Message["ts"])
		if ts == "" {
			continue
		}
		if current := latest[entry.ChannelID]; current == "" || tsFloat(ts) > tsFloat(current) {
			latest[entry.ChannelID] = ts
		}
	}
	marked := 0
	for channelID, ts := range latest {
		if _, err := client.Request("conversations.mark", map[string]string{"channel": channelID, "ts": ts}, true, http.MethodPost, false); err != nil {
			return err
		}
		_ = eventCacheMarkRead(cachePath, channelID, ts)
		marked++
	}
	fmt.Fprintf(rt.Stdout, "ls_opened messages=%d marked_conversations=%d\n", len(entries), marked)
	return nil
}

func (rt *Runtime) printMessageOpen(entry MessageEntry) {
	rt.printSections([][]kv{{
		{"surface", entry.Surface},
		{"conversation", entry.Conversation},
		{"sender", firstNonEmpty(str(entry.Sender["name"]), str(entry.Sender["id"]), "-")},
		{"date", formatTS(str(entry.Message["ts"]))},
		{"message_id", messageID(entry.ChannelID, str(entry.Message["ts"]))},
		{"text", messageText(entry.Message)},
		{"attachments", summarizeAttachments(entry.Message)},
	}})
}

func (rt *Runtime) inspectSlack(args Args, client SlackClient) error {
	if args.Command == "inspect-message" {
		channelID, ts, ok := parseMessageID(args.Recipient)
		if !ok {
			return UsageError{Message: "Use: slack <preset> inspect message <message_id>"}
		}
		data, err := client.Request("conversations.history", map[string]string{
			"channel":   channelID,
			"latest":    ts,
			"inclusive": "true",
			"limit":     "1",
		}, false, http.MethodGet, false)
		if err != nil {
			return err
		}
		messages := asList(data["messages"])
		if len(messages) == 0 {
			return fmt.Errorf("Message not found: %s", args.Recipient)
		}
		message := asMap(messages[0])
		if args.OutputJSON {
			return rt.printJSON(map[string]any{"message": message})
		}
		entry := MessageEntry{ChannelID: channelID, DMID: channelID, Surface: conversationSurface(map[string]any{}, channelID), Conversation: channelID, Message: message, Sender: senderInfo(client, message, nil), SortTS: tsFloat(ts)}
		rt.printSections([][]kv{listEntryFields(entry)})
		return nil
	}
	limit := args.ListLimit
	if limit <= 0 {
		limit = defaultListLimit
	}
	data, err := client.Request("conversations.history", map[string]string{
		"channel": args.Recipient,
		"limit":   fmt.Sprintf("%d", limit),
	}, false, http.MethodGet, false)
	if err != nil {
		return err
	}
	if args.OutputJSON {
		return rt.printJSON(data)
	}
	var rows [][]kv
	for _, raw := range asList(data["messages"]) {
		message := asMap(raw)
		entry := MessageEntry{ChannelID: args.Recipient, DMID: args.Recipient, Surface: conversationSurface(map[string]any{}, args.Recipient), Conversation: args.Recipient, Message: message, Sender: senderInfo(client, message, nil), SortTS: tsFloat(str(message["ts"]))}
		rows = append(rows, listEntryFields(entry))
	}
	if len(rows) == 0 {
		fmt.Fprintln(rt.Stdout, "No messages found.")
		return nil
	}
	rt.printSections(rows)
	return nil
}

func (rt *Runtime) openMessages(recipient string, client SlackClient, selfUserID, cachePath string) error {
	if channelID, ts, ok := parseMessageID(recipient); ok {
		args := Args{Command: "inspect-message", Recipient: recipient}
		if err := rt.inspectSlack(args, client); err != nil {
			return err
		}
		if _, err := client.Request("conversations.mark", map[string]string{"channel": channelID, "ts": ts}, true, http.MethodPost, false); err != nil {
			return err
		}
		_ = eventCacheMarkRead(cachePath, channelID, ts)
		return nil
	}
	row := ConversationRow{ChannelID: recipient, Surface: conversationSurface(map[string]any{}, recipient), Conversation: recipient}
	entries, err := rt.loadConversationMessages(client, row, selfUserID, defaultListLimit, cachePath)
	if err != nil {
		return err
	}
	if len(entries) == 0 {
		fmt.Fprintln(rt.Stdout, "No messages found.")
		return nil
	}
	if err := rt.printOpenEntriesAndMark(entries, client, cachePath); err != nil {
		return err
	}
	return nil
}

func (rt *Runtime) downloadFile(channelID, fileID, outputPath string, client SlackClient) error {
	cursor := ""
	for {
		payload := map[string]string{"channel": channelID, "limit": "200"}
		if cursor != "" {
			payload["cursor"] = cursor
		}
		history, err := client.Request("conversations.history", payload, false, http.MethodGet, false)
		if err != nil {
			return err
		}
		for _, raw := range asList(history["messages"]) {
			message := asMap(raw)
			for _, file := range messageFiles(message) {
				if str(file["id"]) != fileID {
					continue
				}
				downloadURL := str(file["url_private_download"])
				if downloadURL == "" {
					return fmt.Errorf("File has no downloadable URL.")
				}
				destination := firstNonEmpty(outputPath, str(file["name"]), fileID)
				destination = expandPath(destination)
				if err := downloadURLToPath(client, downloadURL, destination); err != nil {
					return err
				}
				fmt.Fprintf(rt.Stdout, "downloaded channel_id=%s file_id=%s path=%s\n", channelID, fileID, destination)
				return nil
			}
		}
		cursor = strings.TrimSpace(str(asMap(history["response_metadata"])["next_cursor"]))
		if cursor == "" {
			break
		}
	}
	return fmt.Errorf("File not found in channel_id=%s: %s", channelID, fileID)
}

func downloadURLToPath(client SlackClient, downloadURL, destination string) error {
	req, err := http.NewRequest(http.MethodGet, downloadURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+client.Token)
	resp, err := client.HTTPClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return fmt.Errorf("download failed: %s", resp.Status)
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil && filepath.Dir(destination) != "." {
		return err
	}
	out, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, resp.Body)
	return err
}

func (rt *Runtime) clearStaleConversations(client SlackClient) error {
	cutoff := float64(time.Now().AddDate(0, -6, 0).Unix())
	counts := map[string]int{"closed": 0, "left": 0, "skipped": 0}
	var rows [][]kv
	dms, _ := listAPI(client, "users.conversations", map[string]string{"types": "im", "exclude_archived": "true", "limit": "200"}, "channels")
	for _, channel := range dms {
		channelID := str(channel["id"])
		if channelID == "" {
			continue
		}
		infoData, err := client.Request("conversations.info", map[string]string{"channel": channelID, "include_num_members": "false"}, false, http.MethodGet, true)
		if err != nil || infoData["ok"] != true {
			continue
		}
		info := asMap(infoData["channel"])
		userID := firstNonEmpty(str(info["user"]), str(channel["user"]), "-")
		user, _ := getUserInfo(client, userID)
		email := userEmail(user, "-")
		latest := extractTS(info)
		var reasons []string
		if email == "-" {
			reasons = append(reasons, "no_email")
		}
		if latest == "" || tsFloat(latest) < cutoff {
			reasons = append(reasons, "stale_6mo")
		}
		if len(reasons) == 0 {
			continue
		}
		action := "closed"
		if _, err := client.Request("conversations.close", map[string]string{"channel": channelID}, true, http.MethodPost, true); err != nil {
			action = "skip:" + err.Error()
			counts["skipped"]++
		} else {
			counts["closed"]++
		}
		rows = append(rows, []kv{{"type", "dm"}, {"action", action}, {"why", strings.Join(reasons, ",")}, {"name", displayUser(user, userID)}, {"email", email}, {"id", channelID}})
	}
	channels, _ := listAPI(client, "conversations.list", map[string]string{"types": "public_channel", "exclude_archived": "true", "limit": "200"}, "channels")
	for _, channel := range channels {
		if !boolValue(channel["is_member"]) {
			continue
		}
		channelID := str(channel["id"])
		if channelID == "" {
			continue
		}
		updated := tsFloat(str(channel["updated"])) / 1000000
		if updated > cutoff {
			continue
		}
		action := "left"
		if boolValue(channel["is_general"]) {
			action = "skip:cant_leave_general"
			counts["skipped"]++
		} else if _, err := client.Request("conversations.leave", map[string]string{"channel": channelID}, true, http.MethodPost, true); err != nil {
			action = "skip:" + err.Error()
			counts["skipped"]++
		} else {
			counts["left"]++
		}
		rows = append(rows, []kv{{"type", "chan"}, {"action", action}, {"why", "stale_6mo"}, {"name", channelName(channel, channelID)}, {"email", "-"}, {"id", channelID}})
	}
	if len(rows) == 0 {
		fmt.Fprintln(rt.Stdout, "No conversations cleared.")
	} else {
		rt.printSections(rows)
	}
	fmt.Fprintf(rt.Stdout, "Summary: closed=%d left=%d skipped=%d private_and_mpim_skipped=scope\n", counts["closed"], counts["left"], counts["skipped"])
	return nil
}

func (rt *Runtime) listMemberChannelsReport(client SlackClient, outputJSON bool) error {
	channels, err := listMemberChannels(client)
	if err != nil {
		return err
	}
	type row struct {
		Surface   string `json:"surface"`
		Name      string `json:"name"`
		ChannelID string `json:"channel_id"`
		Members   string `json:"members,omitempty"`
	}
	var rows []row
	for _, channel := range channels {
		channelID := str(channel["id"])
		if channelID == "" {
			continue
		}
		surface := conversationSurface(channel, channelID)
		if surface != "channel" && surface != "private_channel" {
			continue
		}
		item := row{
			Surface:   surface,
			Name:      channelName(channel, channelID),
			ChannelID: channelID,
		}
		if members := channel["num_members"]; members != nil {
			item.Members = str(members)
		}
		rows = append(rows, item)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Name < rows[j].Name })
	if outputJSON {
		return rt.printJSON(rows)
	}
	if len(rows) == 0 {
		fmt.Fprintln(rt.Stdout, "No member channels found.")
		return nil
	}
	var sections [][]kv
	for _, item := range rows {
		section := []kv{
			{"surface", item.Surface},
			{"name", item.Name},
			{"channel_id", item.ChannelID},
		}
		if item.Members != "" {
			section = append(section, kv{"members", item.Members})
		}
		sections = append(sections, section)
	}
	rt.printSections(sections)
	return nil
}

func (rt *Runtime) listMemberDMsReport(client SlackClient, outputJSON bool) error {
	channels, err := listMemberDMs(client)
	if err != nil {
		return err
	}
	type row struct {
		Surface   string `json:"surface"`
		Name      string `json:"name"`
		ChannelID string `json:"channel_id"`
	}
	var rows []row
	for _, channel := range channels {
		channelID := str(channel["id"])
		if channelID == "" {
			continue
		}
		surface := conversationSurface(channel, channelID)
		if surface != "dm" && surface != "group_dm" {
			continue
		}
		rows = append(rows, row{
			Surface:   surface,
			Name:      channelName(channel, channelID),
			ChannelID: channelID,
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Name < rows[j].Name })
	if outputJSON {
		return rt.printJSON(rows)
	}
	if len(rows) == 0 {
		fmt.Fprintln(rt.Stdout, "No DM or group-DM conversations found.")
		return nil
	}
	var sections [][]kv
	for _, item := range rows {
		sections = append(sections, []kv{
			{"surface", item.Surface},
			{"name", item.Name},
			{"channel_id", item.ChannelID},
		})
	}
	rt.printSections(sections)
	return nil
}

func (rt *Runtime) listContactsReport(contacts Contacts, outputJSON bool) error {
	if len(contacts) == 0 {
		if outputJSON {
			return rt.printJSON([]map[string]string{})
		}
		fmt.Fprintln(rt.Stdout, "No contacts registered.")
		return nil
	}
	keys := make([]string, 0, len(contacts))
	for key := range contacts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if outputJSON {
		payload := make([]map[string]string, 0, len(keys))
		for _, key := range keys {
			payload = append(payload, map[string]string{"label": key, "target": contacts[key]})
		}
		return rt.printJSON(payload)
	}
	var rows [][]kv
	for _, key := range keys {
		rows = append(rows, []kv{{"label", key}, {"target", contacts[key]}})
	}
	rt.printSections(rows)
	return nil
}

func rawJSON(value any) string {
	data, _ := json.Marshal(value)
	return string(data)
}
