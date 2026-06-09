package tui

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type Conversation struct {
	ID            string
	Surface       string
	Label         string
	Latest        string
	Unread        int
	HistoryLoaded bool
}

type Message struct {
	ID     string
	Sender string
	Date   string
	Text   string
}

type Loader func() ([]Conversation, error)
type MessageLoader func(Conversation) ([]Message, error)
type Sender func(Conversation, string) error
type Marker func(Conversation) error

type Options struct {
	LoadConversations Loader
	LoadMessages      MessageLoader
	SendMessage       Sender
	MarkRead          Marker
}

type model struct {
	options       Options
	conversations []Conversation
	messages      []Message
	selected      int
	mode          string
	composer      string
	status        string
	err           error
	width         int
	height        int
}

type conversationsLoaded struct {
	rows []Conversation
	err  error
}

type messagesLoaded struct {
	rows []Message
	err  error
}

type sentMessage struct {
	err error
}

func Run(options Options) error {
	m := model{options: options, mode: "conversations", status: "loading"}
	_, err := tea.NewProgram(m, tea.WithAltScreen()).Run()
	return err
}

func (m model) Init() tea.Cmd {
	return m.loadConversations()
}

func (m model) loadConversations() tea.Cmd {
	return func() tea.Msg {
		rows, err := m.options.LoadConversations()
		return conversationsLoaded{rows: rows, err: err}
	}
}

func (m model) loadMessages(row Conversation) tea.Cmd {
	return func() tea.Msg {
		rows, err := m.options.LoadMessages(row)
		return messagesLoaded{rows: rows, err: err}
	}
}

func (m model) sendMessage(row Conversation, text string) tea.Cmd {
	return func() tea.Msg {
		return sentMessage{err: m.options.SendMessage(row, text)}
	}
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch typed := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = typed.Width
		m.height = typed.Height
	case conversationsLoaded:
		m.err = typed.err
		m.conversations = typed.rows
		m.status = ""
	case messagesLoaded:
		m.err = typed.err
		m.messages = typed.rows
		m.status = ""
		if m.options.MarkRead != nil && m.current().ID != "" {
			_ = m.options.MarkRead(m.current())
		}
	case sentMessage:
		m.err = typed.err
		m.composer = ""
		m.status = "sent"
		if typed.err == nil && m.current().ID != "" {
			return m, m.loadMessages(m.current())
		}
	case tea.KeyMsg:
		key := typed.String()
		if m.mode == "compose" {
			switch key {
			case "esc":
				m.mode = "messages"
			case "enter":
				text := strings.TrimSpace(m.composer)
				if text == "" {
					m.mode = "messages"
					return m, nil
				}
				m.mode = "messages"
				m.status = "sending"
				return m, m.sendMessage(m.current(), text)
			case "backspace", "ctrl+h":
				if len(m.composer) > 0 {
					m.composer = m.composer[:len(m.composer)-1]
				}
			default:
				if len(key) == 1 {
					m.composer += key
				}
			}
			return m, nil
		}
		switch key {
		case "q", "ctrl+c":
			return m, tea.Quit
		case "r":
			m.status = "loading"
			return m, m.loadConversations()
		case "j", "down":
			if m.mode == "conversations" && m.selected < len(m.conversations)-1 {
				m.selected++
			}
		case "k", "up":
			if m.mode == "conversations" && m.selected > 0 {
				m.selected--
			}
		case "enter", "l":
			if m.mode == "conversations" && m.current().ID != "" {
				m.mode = "messages"
				m.status = "loading messages"
				return m, m.loadMessages(m.current())
			}
		case "h", "esc":
			if m.mode == "messages" {
				m.mode = "conversations"
			}
		case "i":
			if m.mode == "messages" {
				m.mode = "compose"
			}
		}
	}
	return m, nil
}

func (m model) current() Conversation {
	if len(m.conversations) == 0 || m.selected < 0 || m.selected >= len(m.conversations) {
		return Conversation{}
	}
	return m.conversations[m.selected]
}

func (m model) View() string {
	if m.width <= 0 {
		m.width = 100
	}
	var b strings.Builder
	titleStyle := lipgloss.NewStyle().Bold(true)
	selectedStyle := lipgloss.NewStyle().Reverse(true)
	muted := lipgloss.NewStyle().Foreground(lipgloss.Color("241"))
	if m.mode == "conversations" {
		b.WriteString(titleStyle.Render("slack tui  conversations"))
		if m.status != "" {
			b.WriteString("  " + muted.Render(m.status))
		}
		b.WriteString("\n\n")
		if m.err != nil {
			b.WriteString("error: " + m.err.Error() + "\n")
		}
		if len(m.conversations) == 0 {
			b.WriteString("No recent DM/GDM conversations.\n")
		}
		for i, row := range m.conversations {
			prefix := "  "
			if row.Unread > 0 {
				prefix = "* "
			}
			line := fmt.Sprintf("%s%-10s %-42s %s", prefix, row.Surface, clip(row.Label, 42), row.Latest)
			if i == m.selected {
				line = selectedStyle.Render(line)
			}
			b.WriteString(line + "\n")
		}
		b.WriteString("\n" + muted.Render("j/k move  enter open  r refresh  q quit"))
		return b.String()
	}
	row := m.current()
	b.WriteString(titleStyle.Render("slack tui  " + row.Label))
	if m.status != "" {
		b.WriteString("  " + muted.Render(m.status))
	}
	b.WriteString("\n\n")
	if m.err != nil {
		b.WriteString("error: " + m.err.Error() + "\n")
	}
	for _, message := range m.messages {
		b.WriteString(muted.Render(message.Date+"  "+message.Sender) + "\n")
		b.WriteString(wrap(message.Text, max(20, m.width-4)) + "\n\n")
	}
	if m.mode == "compose" {
		b.WriteString("> " + m.composer)
	} else {
		b.WriteString(muted.Render("i compose  h back  r refresh  q quit"))
	}
	return b.String()
}

func clip(value string, width int) string {
	if len(value) <= width {
		return value
	}
	if width <= 1 {
		return value[:width]
	}
	return value[:width-1] + "..."
}

func wrap(value string, width int) string {
	words := strings.Fields(value)
	if len(words) == 0 {
		return "-"
	}
	var lines []string
	line := ""
	for _, word := range words {
		if len(line)+1+len(word) > width && line != "" {
			lines = append(lines, line)
			line = word
			continue
		}
		if line == "" {
			line = word
		} else {
			line += " " + word
		}
	}
	if line != "" {
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
