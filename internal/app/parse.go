package app

import (
	"fmt"
	"strconv"
	"strings"
)

const helpText = `Slack CLI

agent quick reference:
  slack <preset> list channels [output json]     channel ids for send to #name or C...
  slack <preset> list dms [output json]          DM and group-DM conversation ids
  slack <preset> list contacts [output json]     saved contact labels
  slack <preset> list [messages] [filters...]    message history (default: messages)
  slack <preset> inspect message <message_id>    read metadata, no side effects
  slack <preset> preview send to <target> body <text> [attach <path>...]
  slack <preset> send to <target> body <text> [attach <path>...]   new top-level post
  slack <preset> reply to <message_id> body <text> [attach <path>...]   thread only
  slack 1 inspect message <message_id>
  slack 1 preview send to <target> body <text>

global:
  slack help | version | upgrade
  slack accounts list [output json]
  slack setup check [output json]
  slack config
  slack auth | slack auth <preset> import | slack auth <preset> bot <token> [user <token>] [app <token>] [name <name>]
  slack mark all read
  slack <preset> mark all read

list (directories vs message history):
  slack <preset> list channels [output json]
  slack <preset> list dms [output json]
  slack <preset> list contacts [output json]
  slack <preset> list [messages] [unread|read] [for <label>] [from <name>] [containing <text>]
              [since <window>] [limit <count>] [output json]

read:
  slack <preset> inspect conversation <channel_id>
  slack <preset> inspect message <message_id>
  slack <preset> open conversation <channel_id> | open message <message_id> | open tui

write:
  slack <preset> preview send to <label|email|#channel|channel_id> body <message> [attach <path>...]
  slack <preset> send to <label|email|#channel|channel_id> body <message> [attach <path>...]
  slack <preset> preview reply to <channel_id>:<ts> body <message> [attach <path>...]
  slack <preset> reply to <channel_id>:<ts> body <message> [attach <path>...]

people and contacts:
  slack <preset> contacts add <label> <email>
  slack <preset> users search <query>

files:
  slack <preset> files download <channel_id> <file_id> [to <path>]

maintenance:
  slack <preset> events sync|once|service|status|logs|reset cache|timer install|timer disable|timer status
  slack <preset> conversations clean

workflow:
  use setup check when preset identity is uncertain; inspect before open; preview before send or reply;
  trailing output json when another agent parses rows
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
		return parseConversationsArgs(args, remaining)
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
		return args, UsageError{Message: "Use: slack <preset> list contacts [output json]"}
	}
	if len(remaining) == 3 && remaining[0] == "add" {
		args.Command = "ac"
		args.Label = remaining[1]
		args.Email = remaining[2]
		return args, nil
	}
	return args, UsageError{Message: "Use: slack <preset> contacts add <label> <email>"}
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

func parseConversationsArgs(args Args, remaining []string) (Args, error) {
	if len(remaining) == 1 && remaining[0] == "list" {
		return args, UsageError{Message: "Use: slack <preset> list channels [output json]"}
	}
	if len(remaining) == 1 && remaining[0] == "clean" {
		args.Command = "sc"
		return args, nil
	}
	return args, UsageError{Message: "Use: slack <preset> conversations clean"}
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
	shape := listUsage()
	remaining, outputJSON, err := extractOutputJSON(remaining, shape)
	if err != nil {
		return args, err
	}
	args.OutputJSON = outputJSON
	if len(remaining) > 0 {
		switch remaining[0] {
		case "channels":
			rest, extraJSON, err := extractOutputJSON(remaining[1:], "Use: slack <preset> list channels [output json]")
			if err != nil {
				return args, err
			}
			if len(rest) > 0 {
				return args, UsageError{Message: "Use: slack <preset> list channels [output json]"}
			}
			args.Command = "list-channels"
			args.OutputJSON = outputJSON || extraJSON
			return args, nil
		case "dms":
			rest, extraJSON, err := extractOutputJSON(remaining[1:], "Use: slack <preset> list dms [output json]")
			if err != nil {
				return args, err
			}
			if len(rest) > 0 {
				return args, UsageError{Message: "Use: slack <preset> list dms [output json]"}
			}
			args.Command = "list-dms"
			args.OutputJSON = outputJSON || extraJSON
			return args, nil
		case "contacts":
			rest, extraJSON, err := extractOutputJSON(remaining[1:], "Use: slack <preset> list contacts [output json]")
			if err != nil {
				return args, err
			}
			if len(rest) > 0 {
				return args, UsageError{Message: "Use: slack <preset> list contacts [output json]"}
			}
			args.Command = "list-contacts"
			args.OutputJSON = outputJSON || extraJSON
			return args, nil
		case "messages":
			remaining = remaining[1:]
		}
	}
	args.Command = "ls"
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
	return "Use: slack <preset> list channels|dms|contacts [output json] | slack <preset> list [messages] [unread|read] [for <label>] [from <name>] [containing <text>] [since <window>] [limit <count>] [output json]"
}
