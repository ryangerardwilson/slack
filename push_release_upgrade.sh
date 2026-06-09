#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

APP=slack
REMOTE=origin
REPO_SLUG=""
ASSET_NAME="slack-linux-x64.tar.gz"
TMP_RELEASE_DIR=""

usage() {
  cat <<'EOF'
Push the current commit, create the next release tag, publish a Linux x64
release artifact, then upgrade the local install from GitHub.

Usage:
  ./push_release_upgrade.sh
  ./push_release_upgrade.sh 0.1.0
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
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

build_release_asset() {
  local version="$1"
  local out_dir="$2"
  local binary_path="$out_dir/$APP"

  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags "-s -w -X github.com/ryangerardwilson/slack/internal/version.Version=${version}" \
    -o "$binary_path" ./cmd/slack

  chmod 755 "$binary_path"
  tar -C "$out_dir" -czf "$out_dir/$ASSET_NAME" "$APP"
}

main() {
  if [[ "${1:-}" == "help" ]]; then
    usage
    exit 0
  fi
  [[ $# -le 1 ]] || die "Too many arguments"

  require_command git
  require_command go
  require_command gh
  require_command tar

  REPO_SLUG="$(remote_repo_slug)"
  require_clean_tree

  local branch
  branch="$(current_branch)"
  [[ -n "$branch" ]] || die "Release from a branch, not detached HEAD"

  info "Running tests..."
  go test ./...

  info "Pushing ${branch}..."
  git push "$REMOTE" "HEAD:${branch}"

  local version
  version="${1:-}"
  if [[ -z "$version" ]]; then
    version="$(next_patch_version "$(latest_remote_version)")"
  fi
  version="${version#v}"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Version must look like 0.1.0"

  local tag="v${version}"
  git show-ref --verify --quiet "refs/tags/${tag}" && die "Local tag ${tag} already exists"
  [[ -z "$(git ls-remote --tags --refs "$REMOTE" "refs/tags/${tag}")" ]] || die "Remote tag ${tag} already exists"

  TMP_RELEASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/${APP}_release_XXXXXX")"
  trap 'rm -rf "${TMP_RELEASE_DIR:-}"' EXIT

  info "Building ${ASSET_NAME}..."
  build_release_asset "$version" "$TMP_RELEASE_DIR"

  info "Creating tag ${tag}..."
  git tag -a "$tag" -m "Release ${tag}"
  git push "$REMOTE" "$tag"

  info "Publishing GitHub release ${tag}..."
  gh release create "$tag" "$TMP_RELEASE_DIR/$ASSET_NAME" \
    --repo "$REPO_SLUG" \
    --title "$tag" \
    --notes "Release ${tag}"

  info "Installing ${APP} ${version} from GitHub..."
  bash ./install.sh version "$version"

  info "Released and installed ${APP} ${version}"
}

main "$@"
