package app

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	appName                    = "slack"
	defaultListLimit           = 10
	eventCacheSchemaVersion    = 1
	eventSyncConversationLimit = 20
	eventSocketTimeoutSeconds  = 70
	eventSyncSeconds           = 120
	defaultBotTokenFile        = "~/.openclaw/credentials/slack-bot-token"
	defaultUserTokenFile       = "~/.openclaw/credentials/slack-user-token"
	defaultAppTokenFile        = "~/.openclaw/credentials/slack-app-token"
	installScriptURL           = "https://raw.githubusercontent.com/ryangerardwilson/slack/main/install.sh"
	configBootstrapText        = "{\n  \"accounts\": {}\n}\n"
)

var (
	userIDRE         = regexp.MustCompile(`^[UW][A-Z0-9]+$`)
	conversationIDRE = regexp.MustCompile(`^[CDG][A-Z0-9]+$`)
	messageIDRE      = regexp.MustCompile(`^([CDG][A-Z0-9]+):([0-9]+\.[0-9]+)$`)
	relativeTimeRE   = regexp.MustCompile(`(?i)^(\d+)([dwmy])$`)
	isoDateRE        = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})$`)
	isoMonthRE       = regexp.MustCompile(`^(\d{4})-(\d{2})$`)
	namedMonthRE     = regexp.MustCompile(`(?i)^([A-Za-z]+)[ -]+(\d{4})$`)
)

type Config map[string]any
type Account map[string]any
type Contacts map[string]string

type Args struct {
	Command       string
	Preset        string
	Label         string
	Email         string
	Recipient     string
	Message       string
	FileID        string
	OutputPath    string
	Paths         []string
	OpenMode      bool
	ListLabel     string
	ListRegistry  bool
	ListFilter    string
	ListLimit     int
	ListFrom      string
	ListContains  string
	ListTimeLimit string
	Query         string
	AuthPreset    string
	AuthBotToken  string
	AuthUserToken string
	AuthAppToken  string
	AuthName      string
	AuthImport    bool
	AuthList      bool
	EventsAction  string
	EventsLines   int
	OutputJSON    bool
}

type Runtime struct {
	Stdout     io.Writer
	Stderr     io.Writer
	HTTPClient *http.Client
	Now        func() time.Time
	OpenEditor func(path string, bootstrap string) error
	RunCommand func(name string, args ...string) error
}

type UsageError struct {
	Message string
}

func (e UsageError) Error() string {
	return e.Message
}

func NewRuntime() *Runtime {
	return &Runtime{
		Stdout:     os.Stdout,
		Stderr:     os.Stderr,
		HTTPClient: &http.Client{Timeout: 60 * time.Second},
		Now: func() time.Time {
			return time.Now()
		},
	}
}

func Main(argv []string) int {
	rt := NewRuntime()
	if err := rt.Run(argv); err != nil {
		var usage UsageError
		if errors.As(err, &usage) {
			fmt.Fprintln(rt.Stderr, usage.Message)
			return 2
		}
		fmt.Fprintln(rt.Stderr, err)
		return 1
	}
	return 0
}

func getenv(name string) string {
	return strings.TrimSpace(os.Getenv(name))
}

func expandPath(value string) string {
	if value == "" {
		return value
	}
	if strings.HasPrefix(value, "~") {
		home, err := os.UserHomeDir()
		if err == nil {
			if value == "~" {
				return home
			}
			if strings.HasPrefix(value, "~/") {
				return filepath.Join(home, value[2:])
			}
		}
	}
	return os.ExpandEnv(value)
}

func configPath(override string) string {
	if override != "" {
		return expandPath(override)
	}
	base := getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, appName, "config.json")
}

func stateBaseDir() string {
	base := getenv("XDG_STATE_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(base, appName)
}

func loadConfig(path string) (Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return Config{}, nil
		}
		return nil, err
	}
	if len(strings.TrimSpace(string(data))) == 0 {
		return Config{}, nil
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("invalid config JSON at %s: %w", path, err)
	}
	if cfg == nil {
		cfg = Config{}
	}
	return cfg, nil
}

func saveConfig(path string, cfg Config) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(path, data, 0o600)
}

func accounts(cfg Config) map[string]Account {
	raw, ok := cfg["accounts"].(map[string]any)
	if !ok {
		return map[string]Account{}
	}
	out := map[string]Account{}
	for key, value := range raw {
		if account, ok := value.(map[string]any); ok {
			out[key] = Account(account)
		}
	}
	return out
}

func ensureAccounts(cfg Config) map[string]any {
	raw, ok := cfg["accounts"].(map[string]any)
	if !ok {
		raw = map[string]any{}
		cfg["accounts"] = raw
	}
	return raw
}

func sortedPresetKeys(accts map[string]Account) []string {
	keys := make([]string, 0, len(accts))
	for key := range accts {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		leftInt, leftErr := strconv.Atoi(keys[i])
		rightInt, rightErr := strconv.Atoi(keys[j])
		if leftErr == nil && rightErr == nil {
			return leftInt < rightInt
		}
		return keys[i] < keys[j]
	})
	return keys
}

func selectAccount(cfg Config, preset string) (string, Account, error) {
	accts := accounts(cfg)
	if len(accts) == 0 {
		if preset == "" {
			return "default", Account(cfg), nil
		}
		return "", nil, UsageError{Message: "No account presets configured. Run: slack auth <preset> ..."}
	}
	if preset == "" {
		return "", nil, UsageError{Message: fmt.Sprintf("Missing preset. Use: slack <preset> <command>. Available presets: %s", strings.Join(sortedPresetKeys(accts), ","))}
	}
	account, ok := accts[preset]
	if !ok {
		return "", nil, UsageError{Message: fmt.Sprintf("Unknown preset: %s. Available presets: %s", preset, strings.Join(sortedPresetKeys(accts), ","))}
	}
	return preset, account, nil
}

func contactsForAccount(cfg Config, account Account) Contacts {
	contacts := Contacts{}
	copyContacts := func(raw any) {
		switch typed := raw.(type) {
		case map[string]any:
			for key, value := range typed {
				if text, ok := value.(string); ok && strings.TrimSpace(text) != "" {
					contacts[key] = strings.TrimSpace(text)
				}
			}
		case map[string]string:
			for key, value := range typed {
				if strings.TrimSpace(value) != "" {
					contacts[key] = strings.TrimSpace(value)
				}
			}
		}
	}
	copyContacts(account["contacts"])
	if len(accounts(cfg)) == 0 {
		copyContacts(cfg["contacts"])
	}
	return contacts
}

func saveContact(cfg Config, preset, label, target string) error {
	accts := ensureAccounts(cfg)
	raw, _ := accts[preset].(map[string]any)
	if raw == nil {
		raw = map[string]any{}
		accts[preset] = raw
	}
	contacts, _ := raw["contacts"].(map[string]any)
	if contacts == nil {
		contacts = map[string]any{}
		raw["contacts"] = contacts
	}
	contacts[label] = target
	return nil
}

func asMap(value any) map[string]any {
	if typed, ok := value.(map[string]any); ok {
		return typed
	}
	return map[string]any{}
}

func asList(value any) []any {
	if typed, ok := value.([]any); ok {
		return typed
	}
	return nil
}

func str(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case fmt.Stringer:
		return typed.String()
	case float64:
		if typed == float64(int64(typed)) {
			return strconv.FormatInt(int64(typed), 10)
		}
		return strconv.FormatFloat(typed, 'f', -1, 64)
	case int:
		return strconv.Itoa(typed)
	case int64:
		return strconv.FormatInt(typed, 10)
	case bool:
		if typed {
			return "true"
		}
		return "false"
	default:
		return ""
	}
}

func intValue(value any, fallback int) int {
	switch typed := value.(type) {
	case int:
		return typed
	case int64:
		return int(typed)
	case float64:
		return int(typed)
	case string:
		parsed, err := strconv.Atoi(strings.TrimSpace(typed))
		if err == nil {
			return parsed
		}
	}
	return fallback
}

func boolValue(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		return typed == "true" || typed == "1"
	case float64:
		return typed != 0
	case int:
		return typed != 0
	default:
		return false
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" && strings.TrimSpace(value) != "-" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
