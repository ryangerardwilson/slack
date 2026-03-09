#!/usr/bin/env bash

_slack_contacts() {
  python3 - <<'PY'
import json
import os

def config_path():
    base = os.getenv("XDG_CONFIG_HOME")
    if not base:
        base = os.path.expanduser("~/.config")
    return os.path.join(os.path.expanduser(base), "slack", "config.json")

try:
    with open(config_path(), "r", encoding="utf-8") as handle:
        data = json.load(handle) or {}
except Exception:
    data = {}

contacts = data.get("contacts") or {}
if isinstance(contacts, dict):
    for key in sorted(contacts):
        if isinstance(key, str) and key.strip():
            print(key.strip())
PY
}

_slack_complete() {
  local cur prev command
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  command="${COMP_WORDS[1]}"

  if [[ ${COMP_CWORD} -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "-h -v -u -cfg ac dm df ls sc mra" -- "$cur") )
    return 0
  fi

  if [[ ${COMP_WORDS[1]} == "-cfg" && ${COMP_CWORD} -eq 2 ]]; then
    COMPREPLY=( $(compgen -f -- "$cur") )
    return 0
  fi

  if [[ ${COMP_WORDS[1]} == "-cfg" ]]; then
    command="${COMP_WORDS[3]}"
    if [[ ${COMP_CWORD} -eq 2 ]]; then
      COMPREPLY=( $(compgen -f -- "$cur") )
      return 0
    fi
    if [[ ${COMP_CWORD} -eq 3 ]]; then
      COMPREPLY=( $(compgen -W "ac dm df ls sc mra" -- "$cur") )
      return 0
    fi
  fi

  case "$command" in
    ac)
      if [[ $prev == "ac" ]]; then
        return 0
      fi
      ;;
    dm)
      if [[ $prev == "dm" ]]; then
        COMPREPLY=( $(compgen -W "$(_slack_contacts)" -- "$cur") )
        return 0
      fi
      if [[ ${COMP_CWORD} -ge 4 ]]; then
        COMPREPLY=( $(compgen -f -- "$cur") )
        return 0
      fi
      ;;
    df)
      if [[ ${COMP_CWORD} -ge 4 ]]; then
        COMPREPLY=( $(compgen -f -- "$cur") )
        return 0
      fi
      ;;
    ls)
      if [[ $prev == "ls" ]]; then
        COMPREPLY=( $(compgen -W "$(_slack_contacts) rc -ur -r" -- "$cur") )
        return 0
      fi
      if [[ $prev == "-ur" || $prev == "-r" ]]; then
        return 0
      fi
      if [[ " ${COMP_WORDS[*]} " != *" -ur "* && " ${COMP_WORDS[*]} " != *" -r "* ]]; then
        COMPREPLY=( $(compgen -W "-ur -r" -- "$cur") )
        return 0
      fi
      ;;
  esac

  return 0
}

complete -F _slack_complete slack
