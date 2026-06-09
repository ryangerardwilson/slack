#!/usr/bin/env bash
set -euo pipefail

APP=slack
REPO="ryangerardwilson/slack"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
PUBLIC_BIN_DIR="$HOME/.local/bin"
PUBLIC_LAUNCHER="$PUBLIC_BIN_DIR/${APP}"
FILENAME="slack-linux-x64.tar.gz"

usage() {
  cat <<EOF
${APP} Installer

Usage:
  install.sh                 Install the latest release
  install.sh help            Show this help
  install.sh version         Print the latest release version
  install.sh version <ver>   Install a specific release version
  install.sh upgrade         Upgrade when a newer release exists
  install.sh from <path>     Install from a local binary or source checkout

EOF
}

die() {
  printf '%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed."
}

normalize_version() {
  local version="$1"
  version="${version#v}"
  [[ -n "$version" ]] || die "empty version"
  printf '%s\n' "$version"
}

installed_command_path() {
  if [[ -x "${INSTALL_DIR}/${APP}" ]]; then
    printf '%s\n' "${INSTALL_DIR}/${APP}"
    return 0
  fi
  if [[ -x "${PUBLIC_LAUNCHER}" ]]; then
    printf '%s\n' "${PUBLIC_LAUNCHER}"
    return 0
  fi
  if command -v "${APP}" >/dev/null 2>&1; then
    command -v "${APP}"
    return 0
  fi
  return 1
}

read_installed_version() {
  local installed_cmd
  installed_cmd="$(installed_command_path)" || return 0
  "$installed_cmd" version 2>/dev/null | head -n 1 | sed 's/^v//' || true
}

get_latest_version() {
  require_command curl
  local release_url
  local tag
  release_url="$(curl -fsSL -o /dev/null -w "%{url_effective}" "https://github.com/${REPO}/releases/latest")" \
    || die "Unable to determine latest release"
  tag="${release_url##*/}"
  tag="${tag#v}"
  [[ -n "$tag" && "$tag" != "latest" ]] || die "Unable to determine latest release"
  printf '%s\n' "$tag"
}

launcher_is_managed() {
  grep -Fq "Managed by ${APP} installer" "$PUBLIC_LAUNCHER" 2>/dev/null && return 0
  grep -Fq "Managed by ${APP} local-bin launcher" "$PUBLIC_LAUNCHER" 2>/dev/null && return 0
  return 1
}

write_public_launcher() {
  mkdir -p "$PUBLIC_BIN_DIR"
  if [[ -e "$PUBLIC_LAUNCHER" && ! -L "$PUBLIC_LAUNCHER" && ! -f "$PUBLIC_LAUNCHER" ]]; then
    die "Refusing to overwrite non-file launcher: $PUBLIC_LAUNCHER"
  fi
  if [[ -f "$PUBLIC_LAUNCHER" ]] && ! launcher_is_managed; then
    die "Refusing to overwrite existing launcher: $PUBLIC_LAUNCHER"
  fi
  cat > "$PUBLIC_LAUNCHER" <<EOF
#!/usr/bin/env bash
# Managed by ${APP} installer local-bin launcher
set -euo pipefail
exec "${INSTALL_DIR}/${APP}" "\$@"
EOF
  chmod 755 "$PUBLIC_LAUNCHER"
}

print_manual_shell_steps() {
  if [[ ":$PATH:" != *":$PUBLIC_BIN_DIR:"* ]]; then
    printf '%s\n' "Manually add to ~/.bashrc if needed: export PATH=$PUBLIC_BIN_DIR:\$PATH"
    printf '%s\n' "Reload your shell: source ~/.bashrc"
  fi
}

install_from_binary() {
  local binary_path=$1
  mkdir -p "$INSTALL_DIR"
  cp "$binary_path" "${INSTALL_DIR}/${APP}"
  chmod 755 "${INSTALL_DIR}/${APP}"
  write_public_launcher
}

source_build_version() {
  local source_path=$1
  local described=""
  if command -v git >/dev/null 2>&1 && git -C "$source_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    described="$(git -C "$source_path" describe --tags --dirty --always --match 'v[0-9]*' 2>/dev/null || true)"
    described="${described#v}"
  fi
  if [[ -z "$described" ]]; then
    described="0.0.0"
  fi
  printf '%s\n' "$described"
}

install_from_source() {
  local source_path=$1
  local build_version
  require_command go
  build_version="$(source_build_version "$source_path")"
  mkdir -p "$INSTALL_DIR"
  (cd "$source_path" && go build \
    -ldflags "-X github.com/ryangerardwilson/slack/internal/version.Version=${build_version}" \
    -o "${INSTALL_DIR}/${APP}" ./cmd/slack)
  chmod 755 "${INSTALL_DIR}/${APP}"
  write_public_launcher
}

install_release() {
  local version
  version="$(normalize_version "$1")"
  require_command curl
  require_command tar

  local os_name arch asset_url tmp_dir archive binary_path
  os_name="$(uname -s)"
  arch="$(uname -m)"
  [[ "$os_name" == "Linux" ]] || die "Only Linux release installs are currently packaged."
  [[ "$arch" == "x86_64" || "$arch" == "amd64" ]] || die "Only Linux x64 release installs are currently packaged."

  asset_url="https://github.com/${REPO}/releases/download/v${version}/${FILENAME}"
  tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/${APP}_install_XXXXXX")"
  archive="$tmp_dir/$FILENAME"

  curl -fsSL "$asset_url" -o "$archive" || die "Unable to download ${asset_url}"
  tar -xzf "$archive" -C "$tmp_dir"
  binary_path="$(find "$tmp_dir" -type f -name "$APP" -perm -u+x | head -n 1)"
  [[ -n "$binary_path" ]] || die "Release archive did not contain executable ${APP}"
  install_from_binary "$binary_path"
  rm -rf "$tmp_dir"
  print_manual_shell_steps
  "${PUBLIC_LAUNCHER}" version
}

upgrade_release() {
  local latest installed
  latest="$(get_latest_version)"
  installed="$(read_installed_version)"
  if [[ -n "$installed" && "$installed" == "$latest" ]]; then
    write_public_launcher
    printf '%s %s already installed\n' "$APP" "$latest"
    return 0
  fi
  install_release "$latest"
}

command_name=${1:-install}
case "$command_name" in
  install)
    install_release "$(get_latest_version)"
    ;;
  help)
    usage
    ;;
  version)
    if [[ -n "${2:-}" ]]; then
      install_release "$2"
    else
      get_latest_version
    fi
    ;;
  upgrade)
    upgrade_release
    ;;
  from)
    source_path=${2:-}
    [[ -n "$source_path" ]] || die "from requires a path"
    if [[ -d "$source_path" ]]; then
      install_from_source "$source_path"
    elif [[ -f "$source_path" ]]; then
      install_from_binary "$source_path"
    else
      die "from path does not exist: $source_path"
    fi
    print_manual_shell_steps
    "${PUBLIC_LAUNCHER}" version
    ;;
  *)
    die "unknown installer command: $command_name"
    ;;
esac
