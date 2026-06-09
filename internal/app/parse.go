package app

import (
	"fmt"
	"strconv"
	"strings"
)

const helpText = `Slack CLI

global actions:
  slack help
    show this help
  slack version
    print the installed version
  slack upgrade
    upgrade to the latest release

features:
  save a contact label for a frequently used Slack recipient
  # <preset> contacts add <label> <email>
  slack 1 contacts add mom mom@example.com
  slack 1 contacts add boss boss@company.com

  edit the saved-contact config directly in your editor
  # config
  slack config

  list configured accounts and run a redacted setup check for agents
  # accounts list | setup check
  slack accounts list
  slack setup check

  configure Slack account presets with tokens stored in config.json
  # slack auth
  # slack auth <preset> import
  # slack auth <preset> bot <bot_token> [user <user_token>] [app <app_token>] [name <name>]
  slack auth
  slack auth 1 import
  slack auth 2 bot xoxb-... user xoxp-... app xapp-... name work

  keep a local realtime DM/GDM event cache for faster list and TUI loads
  # <preset> events sync|once|service|status|logs|reset cache|timer install|timer disable|timer status
  slack 1 events timer install
  slack 1 events status

  send a message from a configured Slack account to a contact, channel, or conversation
  # <preset> send to <label|email|message_id|channel_id> body <message> [attach <path> ...]
  slack 1 preview send to boss@company.com body "latest draft" attach ~/Downloads/draft.pdf
  slack 1 send to mom body "hello"
  slack 1 send to boss@company.com body "latest draft" attach ~/Downloads/draft.pdf
  slack 1 send to C0AE059EU5T body "group update"

  reply in the thread for an exact Slack message id
  # <preset> reply to <message_id> body <message> [attach <path> ...]
  slack 1 preview reply to C0AE059EU5T:1712764800.000100 body "reply in thread"
  slack 1 reply to C0AE059EU5T:1712764800.000100 body "reply in thread"

  download a file attachment from a conversation by channel_id and file_id
  # <preset> files download <channel_id> <file_id> [to <path>]
  slack 1 files download D0466D63H7B F0AH0LD4133

  open a conversation or exact message id, mark it read, show text, download files, and print code blocks
  # <preset> inspect conversation <channel_id> | inspect message <message_id> | open conversation <channel_id> | open message <message_id>
  slack 1 inspect message D0466D63H7B:1712764800.000100
  slack 1 open conversation D0466D63H7B
  slack 1 open message D0466D63H7B:1712764800.000100

  open a keyboard-first terminal view for the latest 100 DM/group-DM messages
  # <preset> open tui
  slack 1 open tui

  list Slack message history with Gmail-style filters, surface labels, and attachment names
  # <preset> list [unread|read] [from <name>] [containing <text>] [since <window>] [limit <count>] [for <label>] [open] [output json]
  slack 1 list limit 10
  slack 1 list unread from maanas since 2w limit 10
  slack 1 list containing invoice since "jan 2025" limit 20
  slack 1 list for md read open limit 5
  slack 1 list for md limit 5 output json

  list all registered contact labels
  # <preset> contacts list
  slack 1 contacts list

  search saved contacts and Slack workspace users
  # <preset> users search <query>
  slack 1 users search rohan
  slack 1 users search "rohan choudhary"

  clear stale conversations and bot-like conversations
  # <preset> conversations clean
  slack 1 conversations clean

  mark all unread DM/GDM notifications as read for one preset, or all presets
  # [<preset>] mark all read
  slack mark all read
  slack 1 mark all read

agent-safe workflow:
  list accounts first, inspect before open when read-state or downloads matter,
  preview before send or reply, and use output json when another agent will
  parse rows
`

func parseArgs(argv []string) (Args, error) {
	args := Args{
		ListFilter:  "all",
		ListLimit:   defaultListLimit,
		EventsLines: 80,
	}
	if len(argv) == 0 {
		return args, nil
	}
	switch argv[0] {
	case "accounts":
		if len(argv) < 2 || argv[1] != "list" {
			return args, UsageError{Message: "Use: slack accounts list [output json]"}
		}
		output, err := parseOptionalOutputJSON(argv[2:], "Use: slack accounts list [output json]")
		args.Command = "accounts-list"
		args.OutputJSON = output
		return args, err
	case "setup":
		if len(argv) < 2 || argv[1] != "check" {
			return args, UsageError{Message: "Use: slack setup check [output json]"}
		}
		output, err := parseOptionalOutputJSON(argv[2:], "Use: slack setup check [output json]")
		args.Command = "setup-check"
		args.OutputJSON = output
		return args, err
	case "config":
		if len(argv) != 1 {
			return args, UsageError{Message: "Use: slack config"}
		}
		args.Command = "config"
		return args, nil
	case "auth":
		return parseAuthArgs(args, argv[1:])
	case "mark":
		if strings.Join(argv[1:], " ") != "all read" {
			return args, UsageError{Message: "Use: slack [<preset>] mark all read"}
		}
		args.Command = "mra"
		return args, nil
	}

	retired := map[string]bool{"cfg": true, "conf": true, "ac": true, "post": true, "dm": true, "reply": true, "df": true, "o": true, "ls": true, "su": true, "u": true, "mra": true, "sc": true}
	if retired[argv[0]] {
		return args, UsageError{Message: "Use declarative Slack commands. Run: slack help"}
	}
	if _, err := strconv.Atoi(argv[0]); err != nil {
		return args, UsageError{Message: topLevelUsage()}
	}
	if len(argv) < 2 {
		return args, UsageError{Message: "Use: slack <preset> <command>"}
	}
	args.Preset = argv[0]
	command := argv[1]
	remaining := argv[2:]
	if retired[command] || command == "tui" {
		return args, UsageError{Message: "Use declarative Slack commands. Run: slack help"}
	}
	switch command {
	case "contacts":
		return parseContactsArgs(args, remaining)
	case "users":
		return parseUsersArgs(args, remaining)
	case "events":
		return parseEventsArgs(args, remaining)
	case "preview":
		return parsePreviewArgs(args, remaining)
	case "inspect":
		return parseInspectArgs(args, remaining)
	case "send":
		return parseSendArgs(args, remaining)
	case "reply":
		return parseReplyArgs(args, remaining)
	case "files":
		return parseFilesArgs(args, remaining)
	case "open":
		return parseOpenArgs(args, remaining)
	case "list":
		return parseListArgs(args, remaining)
	case "conversations":
		if strings.Join(remaining, " ") != "clean" {
			return args, UsageError{Message: "Use: slack <preset> conversations clean"}
		}
		args.Command = "sc"
		return args, nil
	case "mark":
		if strings.Join(remaining, " ") != "all read" {
			return args, UsageError{Message: "Use: slack [<preset>] mark all read"}
		}
		args.Command = "mra"
		return args, nil
	}
	return args, UsageError{Message: topLevelUsage()}
}

func parseOptionalOutputJSON(params []string, shape string) (bool, error) {
	if len(params) == 0 {
		return false, nil
	}
	if len(params) == 2 && params[0] == "output" && params[1] == "json" {
		return true, nil
	}
	return false, UsageError{Message: shape}
}

func extractOutputJSON(params []string, shape string) ([]string, bool, error) {
	if len(params) >= 2 && params[len(params)-2] == "output" && params[len(params)-1] == "json" {
		return params[:len(params)-2], true, nil
	}
	for _, param := range params {
		if param == "output" {
			return nil, false, UsageError{Message: shape}
		}
	}
	return params, false, nil
}

func parseAuthArgs(args Args, remaining []string) (Args, error) {
	args.Command = "auth"
	if len(remaining) == 0 {
		args.AuthList = true
		return args, nil
	}
	args.AuthPreset = remaining[0]
	if args.AuthPreset == "" || strings.HasPrefix(args.AuthPreset, "-") {
		return args, UsageError{Message: "Use: slack auth <preset> import | slack auth <preset> bot <bot_token> [user <user_token>] [app <app_token>] [name <name>]"}
	}
	rest := remaining[1:]
	if len(rest) == 1 && rest[0] == "import" {
		args.AuthImport = true
		return args, nil
	}
	for i := 0; i < len(rest); {
		if i+1 >= len(rest) {
			return args, UsageError{Message: fmt.Sprintf("auth %s requires a value", rest[i])}
		}
		value := rest[i+1]
		switch rest[i] {
		case "bot":
			args.AuthBotToken = value
		case "user":
			args.AuthUserToken = value
		case "app":
			args.AuthAppToken = value
		case "name":
			args.AuthName = value
		default:
			return args, UsageError{Message: "Use: slack auth <preset> bot <bot_token> [user <user_token>] [app <app_token>] [name <name>]"}
		}
		i += 2
	}
	return args, nil
}

func parseContactsArgs(args Args, remaining []string) (Args, error) {
	if len(remaining) == 1 && remaining[0] == "list" {
		args.Command = "ls"
		args.ListRegistry = true
		return args, nil
	}
	if len(remaining) == 3 && remaining[0] == "add" {
		args.Command = "ac"
		args.Label = remaining[1]
		args.Email = remaining[2]
		return args, nil
	}
	return args, UsageError{Message: "Use: slack <preset> contacts list|add <label> <email>"}
}

func parseUsersArgs(args Args, remaining []string) (Args, error) {
	if len(remaining) < 2 || remaining[0] != "search" {
		return args, UsageError{Message: "Use: slack <preset> users search <query>"}
	}
	query := strings.TrimSpace(strings.Join(remaining[1:], " "))
	if query == "" {
		return args, UsageError{Message: "Use: slack <preset> users search <query>"}
	}
	args.Command = "su"
	args.Query = query
	return args, nil
}

func parseEventsArgs(args Args, remaining []string) (Args, error) {
	args.Command = "events"
	if len(remaining) == 0 {
		args.EventsAction = "help"
		return args, nil
	}
	if remaining[0] == "timer" {
		if len(remaining) != 2 {
			return args, UsageError{Message: "Use: slack <preset> events timer install|disable|status"}
		}
		switch remaining[1] {
		case "install":
			args.EventsAction = "ti"
		case "disable":
			args.EventsAction = "td"
		case "status":
			args.EventsAction = "st"
		default:
			return args, UsageError{Message: "Use: slack <preset> events timer install|disable|status"}
		}
		return args, nil
	}
	if len(remaining) == 2 && remaining[0] == "reset" && remaining[1] == "cache" {
		args.EventsAction = "reset-cache"
		return args, nil
	}
	action := remaining[0]
	rest := remaining[1:]
	switch action {
	case "once", "sync", "service", "status":
		if len(rest) != 0 {
			return args, UsageError{Message: fmt.Sprintf("Use: slack <preset> events %s", action)}
		}
		args.EventsAction = action
		return args, nil
	case "logs":
		if len(rest) > 1 {
			return args, UsageError{Message: "Use: slack <preset> events logs [lines]"}
		}
		if len(rest) == 1 {
			value, err := parsePositiveInt(rest[0], "events logs lines")
			if err != nil {
				return args, err
			}
			args.EventsLines = value
		}
		args.EventsAction = action
		return args, nil
	}
	return args, UsageError{Message: "Use: slack <preset> events sync|once|service|status|logs|reset cache|timer install|timer disable|timer status"}
}

func parseBodyAndPaths(remaining []string, shape string) (string, []string, error) {
	if len(remaining) < 2 || remaining[0] != "body" {
		return "", nil, UsageError{Message: shape}
	}
	message := remaining[1]
	var paths []string
	for i := 2; i < len(remaining); {
		if remaining[i] != "attach" {
			return "", nil, UsageError{Message: shape}
		}
		i++
		if i >= len(remaining) {
			return "", nil, UsageError{Message: "attach requires at least one path"}
		}
		for i < len(remaining) && remaining[i] != "attach" {
			paths = append(paths, remaining[i])
			i++
		}
	}
	return message, paths, nil
}

func parseSendArgs(args Args, remaining []string) (Args, error) {
	shape := "Use: slack <preset> send to <target> body <message> [attach <path> ...]"
	if len(remaining) < 4 || remaining[0] != "to" {
		return args, UsageError{Message: shape}
	}
	message, paths, err := parseBodyAndPaths(remaining[2:], shape)
	if err != nil {
		return args, err
	}
	args.Command = "post"
	args.Recipient = remaining[1]
	args.Message = message
	args.Paths = paths
	return args, nil
}

func parseReplyArgs(args Args, remaining []string) (Args, error) {
	shape := "Use: slack <preset> reply to <message_id> body <message> [attach <path> ...]"
	if len(remaining) < 4 || remaining[0] != "to" || !isMessageID(remaining[1]) {
		return args, UsageError{Message: shape}
	}
	message, paths, err := parseBodyAndPaths(remaining[2:], shape)
	if err != nil {
		return args, err
	}
	args.Command = "reply"
	args.Recipient = remaining[1]
	args.Message = message
	args.Paths = paths
	return args, nil
}

func parsePreviewArgs(args Args, remaining []string) (Args, error) {
	if len(remaining) == 0 {
		return args, UsageError{Message: "Use: slack <preset> preview send ... | preview reply ..."}
	}
	switch remaining[0] {
	case "send":
		parsed, err := parseSendArgs(args, remaining[1:])
		parsed.Command = "preview-post"
		return parsed, err
	case "reply":
		parsed, err := parseReplyArgs(args, remaining[1:])
		parsed.Command = "preview-reply"
		return parsed, err
	default:
		return args, UsageError{Message: "Use: slack <preset> preview send ... | preview reply ..."}
	}
}

func parseInspectArgs(args Args, remaining []string) (Args, error) {
	shape := "Use: slack <preset> inspect message <message_id> | inspect conversation <channel_id> [limit <count>] [output json]"
	remaining, outputJSON, err := extractOutputJSON(remaining, shape)
	if err != nil {
		return args, err
	}
	args.OutputJSON = outputJSON
	if len(remaining) < 2 {
		return args, UsageError{Message: shape}
	}
	switch remaining[0] {
	case "message":
		if len(remaining) != 2 {
			return args, UsageError{Message: shape}
		}
		args.Command = "inspect-message"
		args.Recipient = remaining[1]
		return args, nil
	case "conversation":
		args.Command = "inspect-conversation"
		args.Recipient = remaining[1]
		rest := remaining[2:]
		if len(rest) == 0 {
			return args, nil
		}
		if len(rest) == 2 && rest[0] == "limit" {
			value, err := parsePositiveInt(rest[1], "inspect conversation limit")
			args.ListLimit = value
			return args, err
		}
	}
	return args, UsageError{Message: shape}
}

func parseFilesArgs(args Args, remaining []string) (Args, error) {
	shape := "Use: slack <preset> files download <channel_id> <file_id> [to <path>]"
	if (len(remaining) != 3 && len(remaining) != 5) || remaining[0] != "download" {
		return args, UsageError{Message: shape}
	}
	args.Command = "df"
	args.Recipient = remaining[1]
	args.FileID = remaining[2]
	if len(remaining) == 5 {
		if remaining[3] != "to" {
			return args, UsageError{Message: shape}
		}
		args.OutputPath = remaining[4]
	}
	return args, nil
}

func parseOpenArgs(args Args, remaining []string) (Args, error) {
	if len(remaining) == 1 && remaining[0] == "tui" {
		args.Command = "tui"
		return args, nil
	}
	if len(remaining) != 2 || (remaining[0] != "conversation" && remaining[0] != "message") {
		return args, UsageError{Message: "Use: slack <preset> open conversation <channel_id> | open message <message_id> | open tui"}
	}
	args.Command = "o"
	args.Recipient = remaining[1]
	args.OpenMode = true
	return args, nil
}

func parseListArgs(args Args, remaining []string) (Args, error) {
	shape := "Use: slack <preset> list [unread|read] [for <label>] [from <name>] [containing <text>] [since <window>] [limit <count>] [open] [output json]"
	remaining, outputJSON, err := extractOutputJSON(remaining, shape)
	if err != nil {
		return args, err
	}
	args.Command = "ls"
	args.OutputJSON = outputJSON
	for i := 0; i < len(remaining); {
		token := remaining[i]
		switch token {
		case "unread", "read":
			if args.ListFilter != "all" {
				return args, UsageError{Message: listUsage()}
			}
			args.ListFilter = token
			i++
		case "for":
			if i+1 >= len(remaining) {
				return args, UsageError{Message: "for requires: <label>"}
			}
			args.ListLabel = remaining[i+1]
			i += 2
		case "from":
			if i+1 >= len(remaining) {
				return args, UsageError{Message: "from requires: <name>"}
			}
			args.ListFrom = remaining[i+1]
			i += 2
		case "containing":
			if i+1 >= len(remaining) {
				return args, UsageError{Message: "containing requires: <text>"}
			}
			args.ListContains = remaining[i+1]
			i += 2
		case "since":
			if i+1 >= len(remaining) {
				return args, UsageError{Message: "since requires: <window>"}
			}
			args.ListTimeLimit = remaining[i+1]
			i += 2
		case "limit":
			if i+1 >= len(remaining) {
				return args, UsageError{Message: "limit requires: <count>"}
			}
			value, err := parsePositiveInt(remaining[i+1], "list limit")
			if err != nil {
				return args, err
			}
			args.ListLimit = value
			i += 2
		case "open":
			if args.OutputJSON {
				return args, UsageError{Message: "Use either open or output json, not both"}
			}
			args.OpenMode = true
			i++
		default:
			return args, UsageError{Message: "Use: slack <preset> list [unread|read] [for <label>] [from <name>] [containing <text>] [since <window>] [limit <count>] [open]"}
		}
	}
	return args, nil
}

func parsePositiveInt(value, label string) (int, error) {
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return 0, UsageError{Message: fmt.Sprintf("%s must be a positive integer", label)}
	}
	return parsed, nil
}

func topLevelUsage() string {
	return "Use: slack auth | slack auth <preset> import | slack auth <preset> bot <bot_token> [user <user_token>] [app <app_token>] [name <name>] | slack config | slack <preset> contacts add <label> <email> | slack <preset> send to <target> body <message> [attach <path> ...] | slack <preset> reply to <message_id> body <message> [attach <path> ...] | slack <preset> list [unread|read] [from <name>] [since <window>] [limit <count>]"
}

func listUsage() string {
	return "Use: slack <preset> contacts list | slack <preset> list [unread|read] [for <label>] [from <name>] [containing <text>] [since <window>] [limit <count>] [open] [output json]"
}
