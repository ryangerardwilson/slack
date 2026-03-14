#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

APP=""
REMOTE="origin"
REPO_SLUG=""
POLL_ATTEMPTS=60
POLL_INTERVAL_SECONDS=5
INSTALL_ATTEMPTS=12
INSTALL_RETRY_INTERVAL_SECONDS=10

usage() {
  cat <<'EOF'
Push the current app commit, create the next release tag, wait for the GitHub
release to publish, then upgrade the installed app.

Usage:
  ./push_release_upgrade.sh
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

info() {
  echo "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required"
}

require_clean_tree() {
  git diff --quiet --ignore-submodules HEAD -- || die "Commit tracked changes before releasing"
  git diff --cached --quiet --ignore-submodules HEAD -- || die "Commit staged changes before releasing"
  [[ -z "$(git ls-files --others --exclude-standard)" ]] || die "Commit or remove untracked files before releasing"
}

current_branch() {
  git symbolic-ref --quiet --short HEAD || true
}

remote_repo_slug() {
  local remote_url
  remote_url="$(git remote get-url "$REMOTE")"
  remote_url="${remote_url%.git}"
  case "$remote_url" in
    git@github.com:*)
      printf '%s\n' "${remote_url#git@github.com:}"
      ;;
    https://github.com/*)
      printf '%s\n' "${remote_url#https://github.com/}"
      ;;
    http://github.com/*)
      printf '%s\n' "${remote_url#http://github.com/}"
      ;;
    *)
      die "Unsupported remote URL for ${REMOTE}: ${remote_url}"
      ;;
  esac
}

latest_remote_version() {
  git ls-remote --tags --refs "$REMOTE" 'v*' \
    | awk '{print $2}' \
    | sed 's#refs/tags/v##' \
    | awk '/^[0-9]+\.[0-9]+\.[0-9]+$/ { print }' \
    | sort -V \
    | tail -n 1
}

next_patch_version() {
  local latest="$1"
  local major minor patch
  if [[ -z "$latest" ]]; then
    echo "0.1.0"
    return
  fi
  IFS=. read -r major minor patch <<< "$latest"
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ && "$patch" =~ ^[0-9]+$ ]] \
    || die "Unsupported tag format: v$latest"
  echo "${major}.${minor}.$((patch + 1))"
}

release_is_published() {
  local version="$1"
  if command -v gh >/dev/null 2>&1; then
    gh release view "v${version}" -R "$REPO_SLUG" >/dev/null 2>&1 && return 0
  fi
  curl -fsSLo /dev/null "https://github.com/${REPO_SLUG}/releases/tag/v${version}" >/dev/null 2>&1
}

wait_for_release() {
  local version="$1"
  local attempt
  for ((attempt = 1; attempt <= POLL_ATTEMPTS; attempt += 1)); do
    if release_is_published "$version"; then
      return 0
    fi
    sleep "$POLL_INTERVAL_SECONDS"
  done
  die "Timed out waiting for GitHub release v${version} to become latest"
}

run_local_tests() {
  if [[ -d "tests" ]]; then
    python3 -m pytest tests
  else
    info "No tests/ directory; skipping local tests."
  fi
}

install_requested_release() {
  local version="$1"
  local attempt
  local rc
  for ((attempt = 1; attempt <= INSTALL_ATTEMPTS; attempt += 1)); do
    if bash ./install.sh -v "$version"; then
      return 0
    fi
    rc=$?
    if (( attempt == INSTALL_ATTEMPTS )); then
      return "$rc"
    fi
    info "Install artifact for ${APP} ${version} not ready yet; retrying (${attempt}/${INSTALL_ATTEMPTS})..."
    sleep "$INSTALL_RETRY_INTERVAL_SECONDS"
  done
}

verify_installed_version() {
  local version="$1"
  local app_cmd="$HOME/.${APP}/bin/${APP}"
  local installed=""
  if [[ -x "$app_cmd" ]]; then
    installed="$("$app_cmd" -v 2>/dev/null || true)"
  elif command -v "$APP" >/dev/null 2>&1; then
    installed="$("$APP" -v 2>/dev/null || true)"
  else
    return 0
  fi
  installed="${installed#v}"
  [[ "$installed" == "$version" ]] || die "Installed ${APP} version is '$installed', expected '$version'"
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi
  [[ $# -eq 0 ]] || die "This script does not accept arguments"

  require_command git
  require_command bash
  require_command python3

  APP="$(basename "$ROOT_DIR")"
  REPO_SLUG="$(remote_repo_slug)"

  [[ -f "install.sh" ]] || die "install.sh not found in $ROOT_DIR"
  [[ -f ".github/workflows/release.yml" ]] || die "release workflow not found"
  [[ -f "_version.py" ]] || die "_version.py not found"
  grep -qx '__version__ = "0.0.0"' "_version.py" || die "_version.py must remain at 0.0.0 before tagging"

  local branch
  branch="$(current_branch)"
  [[ -n "$branch" ]] || die "Release from a branch, not detached HEAD"

  require_clean_tree

  info "Running local tests..."
  run_local_tests

  info "Pushing ${branch}..."
  git push "$REMOTE" "HEAD:${branch}"

  local latest_version
  local next_version
  local next_tag
  latest_version="$(latest_remote_version)"
  next_version="$(next_patch_version "$latest_version")"
  next_tag="v${next_version}"

  git show-ref --verify --quiet "refs/tags/${next_tag}" && die "Local tag ${next_tag} already exists"
  [[ -z "$(git ls-remote --tags --refs "$REMOTE" "refs/tags/${next_tag}")" ]] \
    || die "Remote tag ${next_tag} already exists"

  info "Creating tag ${next_tag}..."
  git tag -a "$next_tag" -m "Release ${next_tag}"

  info "Pushing ${next_tag}..."
  git push "$REMOTE" "$next_tag"

  info "Waiting for GitHub release ${next_tag}..."
  wait_for_release "$next_version"

  info "Upgrading installed ${APP}..."
  install_requested_release "$next_version"
  verify_installed_version "$next_version"

  info "Released and upgraded ${APP} ${next_version}"
}

main "$@"
