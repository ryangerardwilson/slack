#!/usr/bin/env bash
set -euo pipefail

APP=slack
REPO="ryangerardwilson/slack"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
APP_DIR="$APP_HOME/app"
VENV_DIR="$APP_HOME/venv"

MUTED='\033[0;2m'
RED='\033[0;31m'
ORANGE='\033[38;5;214m'
NC='\033[0m'

usage() {
  cat <<EOF
${APP} Installer

Usage: install.sh [options]

Options:
  -h, --help              Display this help message
  -v, --version <version> Install a specific version (e.g., 0.1.0 or v0.1.0)
  -b, --binary <path>     Install from a local binary instead of downloading
      --no-modify-path    Don't modify shell config files (.zshrc, .bashrc, etc.)

Examples:
  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash -s -- --version 0.1.0
  ./install.sh --binary /path/to/slack
EOF
}

requested_version=${VERSION:-}
no_modify_path=false
binary_path=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -v|--version)
      [[ -n "${2:-}" ]] || { echo -e "${RED}Error: --version requires an argument${NC}"; exit 1; }
      requested_version="$2"
      shift 2
      ;;
    -b|--binary)
      [[ -n "${2:-}" ]] || { echo -e "${RED}Error: --binary requires a path${NC}"; exit 1; }
      binary_path="$2"
      shift 2
      ;;
    --no-modify-path)
      no_modify_path=true
      shift
      ;;
    *)
      echo -e "${ORANGE}Warning: Unknown option '$1'${NC}" >&2
      shift
      ;;
  esac
done

print_message() {
  local level=$1
  local message=$2
  local color="${NC}"
  [[ "$level" == "error" ]] && color="${RED}"
  echo -e "${color}${message}${NC}"
}

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
  command -v python3 >/dev/null 2>&1 || { print_message error "'python3' is required but not installed."; exit 1; }

  filename="${APP}.tar.gz"
  mkdir -p "$APP_DIR"

  if [[ -z "$requested_version" ]]; then
    specific_version="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' || true)"
    if [[ -n "$specific_version" ]]; then
      url="https://github.com/${REPO}/archive/refs/tags/v${specific_version}.tar.gz"
    else
      specific_version="main"
      url="https://github.com/${REPO}/archive/refs/heads/main.tar.gz"
    fi
  else
    requested_version="${requested_version#v}"
    url="https://github.com/${REPO}/archive/refs/tags/v${requested_version}.tar.gz"
    specific_version="${requested_version}"

    http_status=$(curl -sI -o /dev/null -w "%{http_code}" "https://github.com/${REPO}/releases/tag/v${requested_version}")
    if [[ "$http_status" == "404" ]]; then
      print_message error "Release v${requested_version} not found"
      print_message info  "${MUTED}See available releases: ${NC}https://github.com/${REPO}/releases"
      exit 1
    fi
  fi

  if command -v "${APP}" >/dev/null 2>&1 && [[ "$specific_version" != "main" ]]; then
    installed_version=$(${APP} --version 2>/dev/null || true)
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

  extracted_dir=$(find "$tmp_dir" -maxdepth 1 -type d -name "${APP}-*" -print -quit)
  if [[ -z "$extracted_dir" ]]; then
    print_message error "Archive did not contain expected '${APP}-*' directory"
    exit 1
  fi

  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"

  cp "$extracted_dir/main.py" "$APP_DIR/main.py"
  cp "$extracted_dir/requirements.txt" "$APP_DIR/requirements.txt"
  cp "$extracted_dir/_version.py" "$APP_DIR/_version.py"
  if [[ "$specific_version" != "main" && "$specific_version" != "local" ]]; then
    echo "__version__ = \"${specific_version}\"" > "$APP_DIR/_version.py"
  fi

  completion_src="$extracted_dir/completions/slack.bash"
  completion_dst="$APP_HOME/completions/slack.bash"
  if [[ -f "$completion_src" ]]; then
    mkdir -p "$APP_HOME/completions"
    cp "$completion_src" "$completion_dst"
    chmod 644 "$completion_dst"
  fi

  rm -rf "$tmp_dir"

  print_message info "${MUTED}Creating virtualenv at ${NC}${VENV_DIR}"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install -U pip
  "$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

  cat > "${INSTALL_DIR}/${APP}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
"${HOME}/.${APP}/venv/bin/python" "${HOME}/.${APP}/app/main.py" "\$@"
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

add_completion() {
  local config_file=$1
  local command=$2

  if grep -Fxq "$command" "$config_file" 2>/dev/null; then
    print_message info "${MUTED}Completion entry already present in ${NC}$config_file"
  elif [[ -w "$config_file" ]]; then
    {
      echo ""
      echo "# ${APP} completion"
      echo "$command"
    } >> "$config_file"
    print_message info "${MUTED}Added ${NC}${APP}${MUTED} completion to ${NC}$config_file"
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
  if [[ "$current_shell" == "bash" && -f "$APP_HOME/completions/slack.bash" ]]; then
    completion_line="source \"$APP_HOME/completions/slack.bash\""
    if [[ -n "$config_file" ]]; then
      add_completion "$config_file" "$completion_line"
    else
      print_message info "Add this to your shell config:"
      print_message info "  $completion_line"
    fi
  fi
fi

echo ""
print_message info "${MUTED}Installed ${NC}${APP}${MUTED} to ${NC}${INSTALL_DIR}/${APP}"
print_message info "${MUTED}Run:${NC} ${APP} --help"
echo ""
