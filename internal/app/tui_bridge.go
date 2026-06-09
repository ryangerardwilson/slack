package app

import (
	"net/http"

	"github.com/ryangerardwilson/slack/internal/tui"
)

func (rt *Runtime) runTUI(client SlackClient, selfUserID, cachePath string) error {
	return tui.Run(tui.Options{
		LoadConversations: func() ([]tui.Conversation, error) {
			rows, err := rt.loadRecentConversations(client, selfUserID, cachePath, 100, true)
			if err != nil {
				return nil, err
			}
			out := make([]tui.Conversation, 0, len(rows))
			for _, row := range rows {
				out = append(out, tui.Conversation{
					ID:            row.ChannelID,
					Surface:       row.Surface,
					Label:         row.Conversation,
					Latest:        formatTS(row.LatestTS),
					Unread:        row.Unread,
					HistoryLoaded: row.HistoryLoaded,
				})
			}
			return out, nil
		},
		LoadMessages: func(row tui.Conversation) ([]tui.Message, error) {
			messages, err := rt.loadConversationMessages(client, ConversationRow{
				ChannelID:     row.ID,
				Surface:       row.Surface,
				Conversation:  row.Label,
				LatestTS:      "",
				HistoryLoaded: row.HistoryLoaded,
			}, selfUserID, 100, cachePath)
			if err != nil {
				return nil, err
			}
			out := make([]tui.Message, 0, len(messages))
			for _, message := range messages {
				out = append(out, tui.Message{
					ID:     messageID(message.ChannelID, str(message.Message["ts"])),
					Sender: firstNonEmpty(str(message.Sender["name"]), str(message.Sender["id"]), "-"),
					Date:   formatTS(str(message.Message["ts"])),
					Text:   messageText(message.Message),
				})
			}
			return out, nil
		},
		SendMessage: func(row tui.Conversation, text string) error {
			_, err := sendPost(client, row.ID, text, "")
			return err
		},
		MarkRead: func(row tui.Conversation) error {
			messages, err := eventCacheLoadChannelEntries(cachePath, row.ID, selfUserID, 100)
			if err != nil {
				return err
			}
			latest := ""
			for _, message := range messages {
				ts := str(message.Message["ts"])
				if tsFloat(ts) > tsFloat(latest) {
					latest = ts
				}
			}
			if latest == "" {
				return nil
			}
			if _, err := client.Request("conversations.mark", map[string]string{"channel": row.ID, "ts": latest}, true, http.MethodPost, true); err != nil {
				return err
			}
			return eventCacheMarkRead(cachePath, row.ID, latest)
		},
	})
}
