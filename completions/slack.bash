#!/usr/bin/env bash

_slack_dm_labels() {
  python3 - <<'PY'
import json
import os

def config_path():
    base = os.getenv("XDG_CONFIG_HOME")
    if not base:
        base = os.path.expanduser("~/.config")
    base = os.path.expanduser(base)
    return os.path.join(base, "slack", "config.json")

path = config_path()
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle) or {}
except Exception:
    data = {}

labels = data.get("user_labels") or {}
if isinstance(labels, dict):
    for key in labels.keys():
        if isinstance(key, str) and key.strip():
            print(key.strip())
PY
}

_slack_dm_is_python() {
  [[ $1 =~ ^python([0-9]+(\.[0-9]+)*)?$ ]]
}

_slack_dm_is_main_py() {
  [[ $1 == "main.py" || $1 == */main.py ]]
}

_slack_dm_complete() {
  local cur prev options
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"

  options="-e --edit -v --version -u --upgrade -h --help -au --add-user --config"

  if [[ $cur == -* ]]; then
    COMPREPLY=( $(compgen -W "$options" -- "$cur") )
    return 0
  fi

  if [[ $prev == "--config" ]]; then
    COMPREPLY=( $(compgen -f -- "$cur") )
    return 0
  fi

  if [[ $prev == "-au" || $prev == "--add-user" ]]; then
    return 0
  fi

  local cmd_offset=1
  if _slack_dm_is_python "${COMP_WORDS[0]}" && _slack_dm_is_main_py "${COMP_WORDS[1]}"; then
    cmd_offset=2
  fi

  if [[ ${COMP_CWORD} -eq $cmd_offset ]]; then
    COMPREPLY=( $(compgen -W "$(_slack_dm_labels)" -- "$cur") )
    return 0
  fi

  if [[ ${COMP_CWORD} -gt $cmd_offset ]]; then
    local idx=$cmd_offset
    local seen_positional=0
    while [[ $idx -lt ${COMP_CWORD} ]]; do
      case "${COMP_WORDS[$idx]}" in
        -e|--edit|-v|--version|-u|--upgrade|-h|--help)
          ;;
        --config)
          idx=$((idx + 1))
          ;;
        -au|--add-user)
          idx=$((idx + 2))
          ;;
        *)
          seen_positional=1
          ;;
      esac
      idx=$((idx + 1))
    done

    if [[ $seen_positional -eq 0 ]]; then
      COMPREPLY=( $(compgen -W "$(_slack_dm_labels)" -- "$cur") )
      return 0
    fi
  fi

  return 0
}

complete -F _slack_dm_complete slack

_slack_dm_complete_python() {
  if _slack_dm_is_main_py "${COMP_WORDS[1]}"; then
    _slack_dm_complete
    return 0
  fi
  return 0
}

complete -F _slack_dm_complete_python python
complete -F _slack_dm_complete_python python3
