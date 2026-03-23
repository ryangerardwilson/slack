#!/usr/bin/env bash
set -euo pipefail

APP=slack
REPO="ryangerardwilson/slack"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
APP_DIR="$APP_HOME/app"
FILENAME="slack-linux-x64.tar.gz"
PUBLIC_BIN_DIR="$HOME/.local/bin"
PUBLIC_LAUNCHER="$PUBLIC_BIN_DIR/${APP}"


usage() {
  cat <<EOF
${APP} Installer

Usage: install.sh [options]

Options:
  -h                         Show this help and exit
  -v [<version>]             Print the latest release version, or install a specific one
  -u                         Upgrade to the latest release only when newer
  -b <path>                  Install from a local binary instead of downloading
  -n                         Do not modify shell config to add to PATH

      --help                 Compatibility alias for -h
      --version [<version>]  Compatibility alias for -v
      --upgrade              Compatibility alias for -u
      --binary <path>        Compatibility alias for -b
      --no-modify-path       Compatibility alias for -n
EOF
}

requested_version=${VERSION:-}
show_latest=false
upgrade=false
no_modify_path=false
binary_path=""
latest_version_cache=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -v|--version)
      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
        requested_version="${2#v}"
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
      [[ -n "${2:-}" ]] || { echo "Error: -b requires a path"; exit 1; }
      binary_path="$2"
      shift 2
      ;;
    -n|--no-modify-path)
      no_modify_path=true
      shift
      ;;
    *)
      echo "Warning: Unknown option '$1'" >&2
      shift
      ;;
  esac
done

print_message() {
  local level=$1
  local message=$2
  printf '%b\n' "$message"
}

die() {
  print_message error "$1"
  exit 1
}

installed_command_path() {
  if command -v "${APP}" >/dev/null 2>&1; then
    command -v "${APP}"
    return 0
  fi
  if [[ -x "${INSTALL_DIR}/${APP}" ]]; then
    printf '%s\n' "${INSTALL_DIR}/${APP}"
    return 0
  fi
  if [[ -x "${PUBLIC_LAUNCHER}" ]]; then
    printf '%s\n' "${PUBLIC_LAUNCHER}"
    return 0
  fi
  return 1
}

read_installed_version() {
  local installed_cmd
  installed_cmd="$(installed_command_path)" || return 0
  "$installed_cmd" -v 2>/dev/null || true
}

get_latest_version() {
  command -v curl >/dev/null 2>&1 || die "'curl' is required but not installed."
  if [[ -z "$latest_version_cache" ]]; then
    local release_url
    local tag
    release_url="$(curl -fsSL -o /dev/null -w "%{url_effective}" "https://github.com/${REPO}/releases/latest")" \
      || die "Unable to determine latest release"
    tag="${release_url##*/}"
    tag="${tag#v}"
    [[ -n "$tag" && "$tag" != "latest" ]] || die "Unable to determine latest release"
    latest_version_cache="$tag"
  fi
  printf '%s\n' "$latest_version_cache"
}


write_public_launcher() {
  if [[ -e "$PUBLIC_LAUNCHER" && ! -L "$PUBLIC_LAUNCHER" && ! -f "$PUBLIC_LAUNCHER" ]]; then
    die "Refusing to overwrite non-file launcher: $PUBLIC_LAUNCHER"
  fi

  if [[ -L "$PUBLIC_LAUNCHER" ]]; then
    local resolved
    resolved="$(readlink -f "$PUBLIC_LAUNCHER" 2>/dev/null || true)"
    if [[ "$resolved" != "${INSTALL_DIR}/${APP}" ]]; then
      die "Refusing to overwrite existing symlink launcher: $PUBLIC_LAUNCHER"
    fi
  elif [[ -f "$PUBLIC_LAUNCHER" ]] && ! grep -Fq '# Managed by rgw_cli_contract local-bin launcher' "$PUBLIC_LAUNCHER" 2>/dev/null; then
    die "Refusing to overwrite existing launcher: $PUBLIC_LAUNCHER"
  fi

  mkdir -p "$PUBLIC_BIN_DIR"
  cat > "${PUBLIC_LAUNCHER}" <<EOF
#!/usr/bin/env bash
# Managed by rgw_cli_contract local-bin launcher
set -euo pipefail
exec "${INSTALL_DIR}/${APP}" "\$@"
EOF
  chmod 755 "${PUBLIC_LAUNCHER}"
}

finalize_install() {
  write_public_launcher
}

print_manual_shell_steps() {
  local printed=false
  if [[ ":$PATH:" != *":$PUBLIC_BIN_DIR:"* ]]; then
    print_message info "Manually add to ~/.bashrc if needed: export PATH=$PUBLIC_BIN_DIR:\$PATH"
    printed=true
  fi
  if [[ "$printed" == "true" ]]; then
    print_message info "Reload your shell: source ~/.bashrc"
  fi
}

if $show_latest; then
  [[ "$upgrade" == false && -z "$binary_path" && -z "$requested_version" ]] || \
    die "-v (no arg) cannot be combined with other options"
  get_latest_version
  exit 0
fi

if $upgrade; then
  [[ -z "$binary_path" ]] || die "-u cannot be used with -b"
  [[ -z "$requested_version" ]] || die "-u cannot be combined with -v <version>"
  requested_version="$(get_latest_version)"
  installed_version="$(read_installed_version)"
  installed_version="${installed_version#v}"
  if [[ -n "$installed_version" && "$installed_version" == "$requested_version" ]]; then
    finalize_install
    print_manual_shell_steps
    print_message info "${APP} version ${requested_version} already installed"
    exit 0
  fi
fi

mkdir -p "$INSTALL_DIR"

if [[ -n "$binary_path" ]]; then
  [[ -f "$binary_path" ]] || { print_message error "Binary not found: $binary_path"; exit 1; }
  print_message info "\nInstalling ${APP} from local binary: ${binary_path}"
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

  mkdir -p "$APP_DIR"

  if [[ -z "$requested_version" ]]; then
    specific_version="$(get_latest_version)"
  else
    requested_version="${requested_version#v}"
    specific_version="${requested_version}"
    http_status=$(curl -sI -o /dev/null -w "%{http_code}" "https://github.com/${REPO}/releases/tag/v${requested_version}")
    if [[ "$http_status" == "404" ]]; then
      print_message error "Release v${requested_version} not found"
      print_message info "See available releases: https://github.com/${REPO}/releases"
      exit 1
    fi
  fi

  url="https://github.com/${REPO}/releases/download/v${specific_version}/${FILENAME}"

  installed_version="$(read_installed_version)"
  installed_version="${installed_version#v}"
  if [[ -n "$installed_version" && "$installed_version" == "$specific_version" ]]; then
    finalize_install
    print_manual_shell_steps
    print_message info "${APP} version ${specific_version} already installed"
    exit 0
  fi

  print_message info "\nInstalling ${APP} version: ${specific_version}"
  tmp_dir="${TMPDIR:-/tmp}/${APP}_install_$$"
  mkdir -p "$tmp_dir"

  curl -# -L -o "$tmp_dir/$FILENAME" "$url"
  tar -xzf "$tmp_dir/$FILENAME" -C "$tmp_dir"

  if [[ ! -f "$tmp_dir/${APP}/${APP}" ]]; then
    print_message error "Archive did not contain expected directory '${APP}/${APP}'"
    print_message info  "Expected: $tmp_dir/${APP}/${APP}"
    exit 1
  fi


  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  mv "$tmp_dir/${APP}" "$APP_DIR"
  rm -rf "$tmp_dir"

  cat > "${INSTALL_DIR}/${APP}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

"${HOME}/.${APP}/app/${APP}/${APP}" "\$@"
EOF
  chmod 755 "${INSTALL_DIR}/${APP}"
fi

finalize_install

print_manual_shell_steps
print_message info "Run: ${APP} -h"
