#!/usr/bin/env bash
set -euo pipefail

APP=slack
REPO="ryangerardwilson/slack"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
APP_DIR="$APP_HOME/app"
LATEST_RELEASE_API="https://api.github.com/repos/${REPO}/releases/latest"

MUTED='\033[0;2m'
RED='\033[0;31m'
ORANGE='\033[38;5;214m'
NC='\033[0m'

usage() {
  cat <<EOF
${APP} Installer

Usage: install.sh [options]

Options:
  -h, --help           Show this help
  -v, --version        Print the latest release version
  -v <version>         Install a specific version (for example 0.1.0 or v0.1.0)
  -u, --upgrade        Upgrade to the latest release when newer than installed
  -b, --binary <path>  Install from a local binary instead of downloading
      --no-modify-path Do not modify shell config files

Examples:
  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash
  ./install.sh -v
  ./install.sh -v 0.1.0
  ./install.sh -u
  ./install.sh -b /path/to/slack
EOF
}

requested_version=""
show_latest=false
upgrade=false
no_modify_path=false
binary_path=""

print_message() {
  local level=$1
  local message=$2
  local color="${NC}"
  [[ "$level" == "error" ]] && color="${RED}"
  echo -e "${color}${message}${NC}"
}

normalize_version() {
  local version=${1#v}
  printf '%s\n' "$version"
}

get_latest_version() {
  local latest
  latest="$(
    curl -fsSL "$LATEST_RELEASE_API" \
      | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' \
      | head -n 1
  )"
  [[ -n "$latest" ]] || {
    print_message error "Unable to determine the latest release version"
    exit 1
  }
  printf '%s\n' "$(normalize_version "$latest")"
}

get_installed_version() {
  if ! command -v "${APP}" >/dev/null 2>&1; then
    return 0
  fi
  local installed
  installed="$(${APP} -v 2>/dev/null || true)"
  [[ -n "$installed" ]] || return 0
  printf '%s\n' "$(normalize_version "$installed")"
}

version_is_newer() {
  local candidate current
  candidate="$(normalize_version "$1")"
  current="$(normalize_version "$2")"
  [[ "$(printf '%s\n%s\n' "$candidate" "$current" | sort -V | tail -n 1)" == "$candidate" && "$candidate" != "$current" ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -v|--version)
      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
        requested_version="$2"
        shift 2
      else
        show_latest=true
        shift
      fi
      ;;
    -u|--upgrade)
      upgrade=true
      shift
      ;;
    -b|--binary)
      [[ -n "${2:-}" ]] || { print_message error "-b requires a path"; exit 1; }
      binary_path="$2"
      shift 2
      ;;
    --binary)
      [[ -n "${2:-}" ]] || { print_message error "--binary requires a path"; exit 1; }
      binary_path="$2"
      shift 2
      ;;
    --no-modify-path)
      no_modify_path=true
      shift
      ;;
    *)
      print_message error "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [[ -n "$requested_version" && "$show_latest" == "true" ]]; then
  print_message error "Use -v with no argument to print the latest release version."
  exit 1
fi

if [[ "$upgrade" == "true" && -n "$requested_version" ]]; then
  print_message error "Do not combine -u with -v <version>."
  exit 1
fi

if [[ "$upgrade" == "true" && -n "$binary_path" ]]; then
  print_message error "Do not combine -u with -b."
  exit 1
fi

if [[ "$show_latest" == "true" && ( "$upgrade" == "true" || -n "$binary_path" ) ]]; then
  print_message error "Use -v by itself to print the latest release version."
  exit 1
fi

if [[ "$show_latest" == "true" ]]; then
  get_latest_version
  exit 0
fi

mkdir -p "$INSTALL_DIR"

if [[ -n "$binary_path" ]]; then
  [[ -f "$binary_path" ]] || { print_message error "Binary not found: $binary_path"; exit 1; }
  print_message info "\n${MUTED}Installing ${NC}${APP}${MUTED} from local binary: ${NC}${binary_path}"
  cp "$binary_path" "${INSTALL_DIR}/${APP}"
  chmod 755 "${INSTALL_DIR}/${APP}"
  specific_version="local"
else
  raw_os=$(uname -s)
  arch=$(uname -m)

  if [[ "$raw_os" != "Linux" ]]; then
    print_message error "Unsupported OS: $raw_os (this installer supports Linux only)"
    exit 1
  fi

  if [[ "$arch" != "x86_64" ]]; then
    print_message error "Unsupported arch: $arch (this installer supports x86_64 only)"
    exit 1
  fi

  command -v curl >/dev/null 2>&1 || { print_message error "'curl' is required but not installed."; exit 1; }
  command -v tar  >/dev/null 2>&1 || { print_message error "'tar' is required but not installed."; exit 1; }

  filename="${APP}-linux-x64.tar.gz"
  mkdir -p "$APP_DIR"

  if [[ "$upgrade" == "true" ]]; then
    latest_version="$(get_latest_version)"
    installed_version="$(get_installed_version)"
    if [[ -n "$installed_version" ]] && ! version_is_newer "$latest_version" "$installed_version"; then
      print_message info "${MUTED}${APP} ${NC}${installed_version}${MUTED} is already the latest installed version${NC}"
      exit 0
    fi
    requested_version="$latest_version"
  fi

  if [[ -z "$requested_version" ]]; then
    latest_version="$(get_latest_version)"
    specific_version="$latest_version"
    url="https://github.com/${REPO}/releases/latest/download/${filename}"
  else
    requested_version="$(normalize_version "$requested_version")"
    url="https://github.com/${REPO}/releases/download/v${requested_version}/${filename}"
    specific_version="${requested_version}"

    http_status=$(curl -sI -o /dev/null -w "%{http_code}" "https://github.com/${REPO}/releases/tag/v${requested_version}")
    if [[ "$http_status" == "404" ]]; then
      print_message error "Release v${requested_version} not found"
      print_message info  "${MUTED}See available releases: ${NC}https://github.com/${REPO}/releases"
      exit 1
    fi
  fi

  if command -v "${APP}" >/dev/null 2>&1; then
    installed_version="$(get_installed_version)"
    if [[ -n "$installed_version" && "$installed_version" == "$specific_version" ]]; then
      print_message info "${MUTED}${APP} version ${NC}${specific_version}${MUTED} already installed${NC}"
      exit 0
    fi
  fi

  print_message info "\n${MUTED}Installing ${NC}${APP} ${MUTED}version: ${NC}${specific_version}"
  tmp_dir="${TMPDIR:-/tmp}/${APP}_install_$$"
  mkdir -p "$tmp_dir"

  curl -# -L -o "$tmp_dir/$filename" "$url"
  tar -xzf "$tmp_dir/$filename" -C "$tmp_dir"

  if [[ ! -f "$tmp_dir/${APP}/${APP}" ]]; then
    print_message error "Archive did not contain expected directory '${APP}/${APP}'"
    print_message info  "Expected: $tmp_dir/${APP}/${APP}"
    exit 1
  fi

  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"

  mv "$tmp_dir/${APP}" "$APP_DIR"
  rm -rf "$tmp_dir"

  completion_src="$APP_DIR/${APP}/completions/slack.bash"
  completion_dst="$APP_HOME/completions/slack.bash"
  if [[ -f "$completion_src" ]]; then
    mkdir -p "$APP_HOME/completions"
    cp "$completion_src" "$completion_dst"
    chmod 644 "$completion_dst"
  fi

  cat > "${INSTALL_DIR}/${APP}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
"${HOME}/.${APP}/app/${APP}/${APP}" "\$@"
EOF
  chmod 755 "${INSTALL_DIR}/${APP}"
fi

add_to_path() {
  local config_file=$1
  local command=$2

  if grep -Fxq "$command" "$config_file" 2>/dev/null; then
    print_message info "${MUTED}PATH entry already present in ${NC}$config_file"
  elif [[ -w "$config_file" ]]; then
    {
      echo ""
      echo "# ${APP}"
      echo "$command"
    } >> "$config_file"
    print_message info "${MUTED}Added ${NC}${APP}${MUTED} to PATH in ${NC}$config_file"
  else
    print_message info "Add this to your shell config:"
    print_message info "  $command"
  fi
}

if [[ "$no_modify_path" != "true" ]]; then
  XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
  current_shell=$(basename "${SHELL:-bash}")

  case "$current_shell" in
    zsh)  config_candidates=("$HOME/.zshrc" "$HOME/.zshenv" "$XDG_CONFIG_HOME/zsh/.zshrc" "$XDG_CONFIG_HOME/zsh/.zshenv") ;;
    bash) config_candidates=("$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$XDG_CONFIG_HOME/bash/.bashrc" "$XDG_CONFIG_HOME/bash/.bash_profile") ;;
    fish) config_candidates=("$HOME/.config/fish/config.fish") ;;
    *)    config_candidates=("$HOME/.profile" "$HOME/.bashrc") ;;
  esac

  config_file=""
  for f in "${config_candidates[@]}"; do
    if [[ -f "$f" ]]; then config_file="$f"; break; fi
  done

  if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    if [[ -z "$config_file" ]]; then
      print_message info "${MUTED}No shell config file found. Manually add:${NC}"
      print_message info "  export PATH=$INSTALL_DIR:\$PATH"
    else
      if [[ "$current_shell" == "fish" ]]; then
        add_to_path "$config_file" "fish_add_path $INSTALL_DIR"
      else
        add_to_path "$config_file" "export PATH=$INSTALL_DIR:\$PATH"
      fi
    fi
  fi
fi

echo ""
print_message info "${MUTED}Installed ${NC}${APP}${MUTED} to ${NC}${INSTALL_DIR}/${APP}"
print_message info "${MUTED}Run:${NC} ${APP} -h"
echo ""
