package app

import (
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/ryangerardwilson/slack/internal/version"
)

func (rt *Runtime) Run(argv []string) error {
	if len(argv) == 0 || (len(argv) == 1 && argv[0] == "help") {
		fmt.Fprint(rt.Stdout, helpText)
		return nil
	}
	if len(argv) == 1 && argv[0] == "version" {
		fmt.Fprintln(rt.Stdout, version.Version)
		return nil
	}
	if len(argv) == 1 && argv[0] == "upgrade" {
		return rt.upgradeApp()
	}
	if argv[0] == "help" || argv[0] == "version" || argv[0] == "upgrade" {
		return UsageError{Message: fmt.Sprintf("Use: slack %s", argv[0])}
	}

	args, err := parseArgs(argv)
	if err != nil {
		return err
	}
	path := configPath("")
	if args.Command == "config" {
		return rt.openConfig(path, configBootstrapText)
	}
	cfg, err := loadConfig(path)
	if err != nil {
		return err
	}
	switch args.Command {
	case "accounts-list":
		return rt.listAccountPresets(cfg, args.OutputJSON)
	case "setup-check":
		return rt.setupCheck(path, cfg, args.OutputJSON)
	case "auth":
		if args.AuthList {
			return rt.listAccountPresets(cfg, false)
		}
		if err := rt.configureAccount(path, cfg, args); err != nil {
			return err
		}
		return nil
	case "mra":
		if args.Preset == "" {
			return rt.markAllConfiguredNotificationsAsRead(cfg)
		}
	}

	preset, account, err := selectAccount(cfg, args.Preset)
	if err != nil {
		return err
	}
	contacts := contactsForAccount(cfg, account)

	if args.Command == "ac" {
		label := strings.TrimSpace(args.Label)
		email := strings.TrimSpace(args.Email)
		if label == "" {
			return UsageError{Message: "Label cannot be empty."}
		}
		if !strings.Contains(email, "@") {
			return UsageError{Message: "Use: slack <preset> contacts add <label> <email>"}
		}
		if err := saveContact(cfg, preset, label, email); err != nil {
			return err
		}
		if err := saveConfig(path, cfg); err != nil {
			return err
		}
		fmt.Fprintf(rt.Stdout, "Saved contact '%s' -> %s\n", label, email)
		return nil
	}
	if args.Command == "" {
		return nil
	}

	switch args.Command {
	case "events":
		return rt.dispatchEvents(account, preset, args)
	case "list-contacts":
		return rt.listContactsReport(contacts, args.OutputJSON)
	case "list-channels":
		listToken, err := resolveListToken(account)
		if err != nil {
			return err
		}
		listClient := rt.slackClient(listToken)
		if _, err := listClient.AuthTest(); err != nil {
			return err
		}
		return rt.listMemberChannelsReport(listClient, args.OutputJSON)
	case "list-dms":
		listToken, err := resolveListToken(account)
		if err != nil {
			return err
		}
		listClient := rt.slackClient(listToken)
		if _, err := listClient.AuthTest(); err != nil {
			return err
		}
		return rt.listMemberDMsReport(listClient, args.OutputJSON)
	case "ls":
		token, err := resolveListToken(account)
		if err != nil {
			return err
		}
		client := rt.slackClient(token)
		auth, err := client.AuthTest()
		if err != nil {
			return err
		}
		selfUserID := str(auth["user_id"])
		if selfUserID == "" {
			return UsageError{Message: "Unable to determine the current Slack user."}
		}
		return rt.listMessages(contacts, client, args, selfUserID, eventCacheDBPath(account, preset))
	case "tui":
		token, err := resolveListToken(account)
		if err != nil {
			return err
		}
		client := rt.slackClient(token)
		auth, err := client.AuthTest()
		if err != nil {
			return err
		}
		selfUserID := str(auth["user_id"])
		if selfUserID == "" {
			return UsageError{Message: "Unable to determine the current Slack user."}
		}
		return rt.runTUI(client, selfUserID, eventCacheDBPath(account, preset))
	case "su":
		token, err := resolveToken(account)
		if err != nil {
			return err
		}
		client := rt.slackClient(token)
		if _, err := client.AuthTest(); err != nil {
			return err
		}
		return rt.searchUsersAndContacts(contacts, client, args.Query)
	case "preview-post", "preview-reply":
		return rt.previewSlackAction(args, contacts)
	case "mra":
		token, err := resolveMarkReadToken(account)
		if err != nil {
			return err
		}
		client := rt.slackClient(token)
		if _, err := client.AuthTest(); err != nil {
			return err
		}
		result, err := rt.markAllUnreadNotificationsAsRead(client, eventCacheDBPath(account, preset), preset)
		if err != nil {
			return err
		}
		if result.Failed > 0 {
			return fmt.Errorf("mark all read failed for %d conversation(s)", result.Failed)
		}
		return nil
	}

	token, err := resolveToken(account)
	if err != nil {
		return err
	}
	client := rt.slackClient(token)
	auth, err := client.AuthTest()
	if err != nil {
		return err
	}
	switch args.Command {
	case "inspect-message", "inspect-conversation":
		return rt.inspectSlack(args, client)
	case "o":
		selfUserID := str(auth["user_id"])
		if selfUserID == "" {
			return UsageError{Message: "Unable to determine the current Slack user."}
		}
		return rt.openMessages(args.Recipient, client, selfUserID, eventCacheDBPath(account, preset))
	case "df":
		return rt.downloadFile(args.Recipient, args.FileID, args.OutputPath, client)
	case "sc":
		return rt.clearStaleConversations(client)
	case "post":
		directToken := resolveDirectPostToken(account, token)
		lookupToken, err := resolveLookupToken(account, directToken)
		if err != nil {
			return err
		}
		directClient := rt.slackClient(directToken)
		lookupClient := rt.slackClient(lookupToken)
		target, err := resolvePostTarget(rt, args.Recipient, contacts, client, lookupClient, directClient)
		if err != nil {
			return err
		}
		postClient := client
		if target.Kind == "email" || target.Kind == "user" {
			postClient = directClient
		}
		message := strings.TrimSpace(args.Message)
		if len(args.Paths) == 0 && message == "" {
			return UsageError{Message: "Use: slack <preset> send to <target> body <message> [attach <path> ...]"}
		}
		var ts string
		var uploaded []string
		if len(args.Paths) > 0 {
			uploaded, ts, err = completeUploadExternalBatch(postClient, target.ChannelID, "", message, args.Paths)
			if err != nil {
				return err
			}
		} else {
			ts, err = sendPost(postClient, target.ChannelID, args.Message, "")
			if err != nil {
				return err
			}
		}
		details := []string{"posted", "target=" + args.Recipient, "kind=" + target.Kind, "channel=" + target.ChannelID}
		if ts != "" {
			details = append(details, "ts="+ts)
		}
		if len(uploaded) > 0 {
			details = append(details, "files="+strings.Join(uploaded, ","))
		}
		fmt.Fprintln(rt.Stdout, strings.Join(details, " "))
		return nil
	case "reply":
		channelID, messageTS, ok := parseMessageID(args.Recipient)
		if !ok {
			return UsageError{Message: "Use: slack <preset> reply to <message_id> body <message> [attach <path> ...]"}
		}
		threadTS, err := resolveReplyThreadTS(client, channelID, messageTS)
		if err != nil {
			return err
		}
		ts, err := sendPost(client, channelID, args.Message, threadTS)
		if err != nil {
			return err
		}
		uploaded, err := sendAttachments(client, channelID, threadTS, args.Paths)
		if err != nil {
			return err
		}
		details := []string{"replied", "message_id=" + args.Recipient, "channel=" + channelID, "thread_ts=" + threadTS}
		if ts != "" {
			details = append(details, "ts="+ts)
		}
		if len(uploaded) > 0 {
			details = append(details, "files="+strings.Join(uploaded, ","))
		}
		fmt.Fprintln(rt.Stdout, strings.Join(details, " "))
		return nil
	}
	return UsageError{Message: topLevelUsage()}
}

func (rt *Runtime) upgradeApp() error {
	cmd := exec.Command("bash", "-c", "curl -fsSL "+installScriptURL+" | bash -s -- upgrade")
	cmd.Stdout = rt.Stdout
	cmd.Stderr = rt.Stderr
	return cmd.Run()
}

func (rt *Runtime) openConfig(path string, bootstrap string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if _, err := os.Stat(path); os.IsNotExist(err) {
		if err := os.WriteFile(path, []byte(bootstrap), 0o600); err != nil {
			return err
		}
	}
	if rt.OpenEditor != nil {
		return rt.OpenEditor(path, bootstrap)
	}
	editor := firstNonEmpty(getenv("EDITOR"), getenv("VISUAL"))
	if editor == "" {
		fmt.Fprintln(rt.Stdout, path)
		return nil
	}
	cmd := exec.Command("sh", "-c", editor+" \"$1\"", "slack-editor", path)
	cmd.Stdin = os.Stdin
	cmd.Stdout = rt.Stdout
	cmd.Stderr = rt.Stderr
	return cmd.Run()
}

func (rt *Runtime) listAccountPresets(cfg Config, outputJSON bool) error {
	accts := accounts(cfg)
	if outputJSON {
		rows := []map[string]any{}
		for _, preset := range sortedPresetKeys(accts) {
			account := accts[preset]
			rows = append(rows, map[string]any{
				"preset":         preset,
				"name":           firstNonEmpty(str(account["name"]), "-"),
				"has_bot_token":  hasToken(account, "bot"),
				"has_user_token": hasToken(account, "user"),
				"has_app_token":  hasToken(account, "app"),
				"contacts":       len(contactsForAccount(cfg, account)),
			})
		}
		return rt.printJSON(map[string]any{"accounts": rows})
	}
	if len(accts) == 0 {
		fmt.Fprintln(rt.Stdout, "No account presets configured.")
		return nil
	}
	var rows [][]kv
	for _, preset := range sortedPresetKeys(accts) {
		account := accts[preset]
		rows = append(rows, []kv{
			{"preset", preset},
			{"name", firstNonEmpty(str(account["name"]), "-")},
			{"bot", redactToken(directToken(account, "bot"))},
			{"user", redactToken(directToken(account, "user"))},
			{"app", redactToken(directToken(account, "app"))},
			{"contacts", fmt.Sprintf("%d", len(contactsForAccount(cfg, account)))},
		})
	}
	rt.printSections(rows)
	return nil
}

func (rt *Runtime) setupCheck(path string, cfg Config, outputJSON bool) error {
	accts := accounts(cfg)
	state := map[string]any{
		"config":          path,
		"exists":          fileExists(path),
		"accounts_count":  len(accts),
		"has_root_tokens": len(tokenMap(cfg)) > 0,
	}
	var rows []map[string]any
	for _, preset := range sortedPresetKeys(accts) {
		account := accts[preset]
		rows = append(rows, map[string]any{
			"preset":         preset,
			"name":           firstNonEmpty(str(account["name"]), "-"),
			"has_bot_token":  hasToken(account, "bot"),
			"has_user_token": hasToken(account, "user"),
			"has_app_token":  hasToken(account, "app"),
			"contacts":       len(contactsForAccount(cfg, account)),
		})
	}
	state["accounts"] = rows
	if outputJSON {
		return rt.printJSON(state)
	}
	fmt.Fprintf(rt.Stdout, "config: %s\nexists: %v\naccounts_count: %d\n", path, state["exists"], len(accts))
	for _, row := range rows {
		fmt.Fprintln(rt.Stdout)
		keys := []string{"preset", "name", "has_bot_token", "has_user_token", "has_app_token", "contacts"}
		for _, key := range keys {
			fmt.Fprintf(rt.Stdout, "%s: %v\n", key, row[key])
		}
	}
	return nil
}

func (rt *Runtime) configureAccount(configPath string, cfg Config, args Args) error {
	accts := ensureAccounts(cfg)
	raw, _ := accts[args.AuthPreset].(map[string]any)
	if raw == nil {
		raw = map[string]any{}
		accts[args.AuthPreset] = raw
	}
	account := Account(raw)
	if args.AuthImport {
		if token := readTokenFile(defaultBotTokenFile); token != "" {
			args.AuthBotToken = token
		}
		if token := readTokenFile(defaultUserTokenFile); token != "" {
			args.AuthUserToken = token
		}
		if token := readTokenFile(defaultAppTokenFile); token != "" {
			args.AuthAppToken = token
		}
	}
	if args.AuthBotToken == "" && args.AuthUserToken == "" && args.AuthAppToken == "" && args.AuthName == "" {
		return UsageError{Message: "Use: slack auth <preset> bot <bot_token> [user <user_token>] [app <app_token>] [name <name>]"}
	}
	if args.AuthBotToken != "" && tokenKind(args.AuthBotToken) != "bot" {
		return UsageError{Message: "bot token must start with xoxb-"}
	}
	if args.AuthUserToken != "" && tokenKind(args.AuthUserToken) != "user" {
		return UsageError{Message: "user token must start with xoxp-"}
	}
	if args.AuthAppToken != "" && tokenKind(args.AuthAppToken) != "app" {
		return UsageError{Message: "app token must start with xapp-"}
	}
	tokenObject := asMap(account["token"])
	if tokenObject == nil {
		tokenObject = map[string]any{}
	}
	if args.AuthBotToken != "" {
		tokenObject["bot"] = args.AuthBotToken
	}
	if args.AuthUserToken != "" {
		tokenObject["user"] = args.AuthUserToken
	}
	if args.AuthAppToken != "" {
		tokenObject["app"] = args.AuthAppToken
	}
	if len(tokenObject) > 0 {
		account["token"] = tokenObject
	}
	if args.AuthName != "" {
		account["name"] = args.AuthName
	}
	if _, ok := account["contacts"]; !ok {
		account["contacts"] = map[string]any{}
	}
	if err := saveConfig(configPath, cfg); err != nil {
		return err
	}
	fmt.Fprintf(rt.Stdout, "Saved Slack account preset %s\n", args.AuthPreset)
	return nil
}

func (rt *Runtime) searchUsersAndContacts(contacts Contacts, client SlackClient, query string) error {
	queryLower := strings.ToLower(query)
	var rows [][]kv
	for label, target := range contacts {
		if strings.Contains(strings.ToLower(label+" "+target), queryLower) {
			rows = append(rows, []kv{{"type", "contact"}, {"label", label}, {"target", target}})
		}
	}
	data, err := client.Request("users.list", map[string]string{"limit": "200"}, false, http.MethodGet, true)
	if err == nil && data["ok"] == true {
		for _, raw := range asList(data["members"]) {
			user := asMap(raw)
			if boolValue(user["deleted"]) || boolValue(user["is_bot"]) {
				continue
			}
			name := displayUser(user, str(user["id"]))
			email := userEmail(user, "-")
			if strings.Contains(strings.ToLower(name+" "+email+" "+str(user["id"])), queryLower) {
				rows = append(rows, []kv{{"type", "user"}, {"name", name}, {"email", email}, {"id", str(user["id"])}})
			}
		}
	}
	if len(rows) == 0 {
		fmt.Fprintln(rt.Stdout, "No Slack users or contacts matched.")
		return nil
	}
	rt.printSections(rows)
	return nil
}

func (rt *Runtime) previewSlackAction(args Args, contacts Contacts) error {
	if len(args.Paths) > 0 {
		for _, path := range args.Paths {
			if _, err := expandExistingPath(path, "attachment"); err != nil {
				return err
			}
		}
	}
	if args.Command == "preview-reply" {
		channelID, messageTS, _ := parseMessageID(args.Recipient)
		rt.printSections([][]kv{{
			{"action", "reply"},
			{"message_id", args.Recipient},
			{"channel", channelID},
			{"thread_ts", messageTS},
			{"body", args.Message},
			{"attachments", strings.Join(args.Paths, ",")},
		}})
		return nil
	}
	target := args.Recipient
	targetKind := "raw"
	if mapped := contacts[target]; mapped != "" {
		targetKind = "contact"
		target = mapped
	} else if conversationIDRE.MatchString(target) {
		targetKind = "channel"
	} else if userIDRE.MatchString(target) {
		targetKind = "user"
	} else if strings.Contains(target, "@") {
		targetKind = "email"
	} else if channelNameQuery(target) != "" {
		targetKind = "channel_name"
	}
	rt.printSections([][]kv{{
		{"action", "send"},
		{"target", args.Recipient},
		{"target_kind", targetKind},
		{"resolved", target},
		{"body", args.Message},
		{"attachments", strings.Join(args.Paths, ",")},
	}})
	return nil
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
