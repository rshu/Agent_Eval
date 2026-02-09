#!/usr/bin/env bash
#
# reset_workspace.sh — Manage evaluation workspaces for coding-agent benchmarks.
#
# Subcommands:
#   prepare   Clone a repo at a specific commit and sanitize git history (anti-cheat).
#   reset     Restore the workspace to the clean base-commit state.
#   apply     Apply a patch file to the workspace.
#   cleanup   Remove the workspace and its metadata directory.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
    echo "ERROR: $*" >&2
    exit 1
}

log() {
    echo "[workspace] $*"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

# Return the meta-directory path for a given workspace.
# Convention: sibling hidden directory  .<basename>_meta/
meta_dir_for() {
    local ws="$1"
    local parent; parent="$(cd "$(dirname "$ws")" && pwd)"
    local base;   base="$(basename "$ws")"
    echo "${parent}/.${base}_meta"
}

# Portable rm -rf that handles Windows read-only files (git pack files).
force_rm() {
    local target="$1"
    if [ -d "$target" ]; then
        # Make everything writable first (Windows git pack files are read-only)
        chmod -R u+w "$target" 2>/dev/null || true
        rm -rf "$target"
    fi
}

# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

cmd_prepare() {
    local repo_url="" base_commit="" workspace="" ground_truth=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --repo-url)      [[ $# -ge 2 ]] || die "prepare: $1 requires a value"; repo_url="$2";      shift 2 ;;
            --base-commit)   [[ $# -ge 2 ]] || die "prepare: $1 requires a value"; base_commit="$2";    shift 2 ;;
            --workspace)     [[ $# -ge 2 ]] || die "prepare: $1 requires a value"; workspace="$2";      shift 2 ;;
            --ground-truth)  [[ $# -ge 2 ]] || die "prepare: $1 requires a value"; ground_truth="$2";   shift 2 ;;
            *) die "prepare: unknown option '$1'" ;;
        esac
    done

    [[ -n "$repo_url" ]]     || die "prepare: --repo-url is required"
    [[ -n "$base_commit" ]]  || die "prepare: --base-commit is required"
    [[ -n "$workspace" ]]    || die "prepare: --workspace is required"
    [[ -n "$ground_truth" ]] || die "prepare: --ground-truth is required"
    [[ -f "$ground_truth" ]] || die "prepare: ground-truth file not found: $ground_truth"

    require_cmd git

    local meta; meta="$(meta_dir_for "$workspace")"

    # 1. Idempotent: remove previous workspace & meta
    log "Cleaning previous workspace (if any)..."
    force_rm "$workspace"
    force_rm "$meta"

    # 2. Clone
    log "Cloning $repo_url ..."
    git clone "$repo_url" "$workspace"

    # 3. Checkout base commit (detached HEAD)
    log "Checking out base commit $base_commit ..."
    git -C "$workspace" checkout "$base_commit" --

    # 4. Anti-cheat: destroy original git history
    log "Sanitizing git history (anti-cheat)..."
    force_rm "$workspace/.git"

    git -C "$workspace" init
    # Ensure git user is configured (may not be set in CI environments)
    git -C "$workspace" config user.name  "${GIT_AUTHOR_NAME:-agent-eval}"
    git -C "$workspace" config user.email "${GIT_AUTHOR_EMAIL:-agent-eval@noreply}"
    git -C "$workspace" add -A
    git -C "$workspace" commit -m "base" --allow-empty

    local clean_hash; clean_hash="$(git -C "$workspace" rev-parse HEAD)"
    log "Clean-state commit: $clean_hash"

    # 5. Store metadata
    mkdir -p "$meta"
    cp "$ground_truth" "$meta/ground_truth.patch"

    cat > "$meta/state.env" <<EOF
CLEAN_HASH=$clean_hash
REPO_URL=$repo_url
BASE_COMMIT=$base_commit
CREATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

    log "Metadata written to $meta/"
    log "Workspace ready: $workspace"
}

# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

cmd_reset() {
    local workspace=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --workspace) [[ $# -ge 2 ]] || die "reset: $1 requires a value"; workspace="$2"; shift 2 ;;
            *) die "reset: unknown option '$1'" ;;
        esac
    done

    [[ -n "$workspace" ]]  || die "reset: --workspace is required"
    [[ -d "$workspace" ]]  || die "reset: workspace directory not found: $workspace"

    local meta; meta="$(meta_dir_for "$workspace")"
    [[ -f "$meta/state.env" ]] || die "reset: metadata not found at $meta/state.env"

    # Safe extraction — avoid sourcing to prevent command injection
    local CLEAN_HASH
    CLEAN_HASH="$(grep -E '^CLEAN_HASH=' "$meta/state.env" | head -1 | cut -d'=' -f2-)"
    [[ -n "$CLEAN_HASH" ]] || die "reset: CLEAN_HASH not set in state.env"
    # Validate it looks like a git hash (hex only)
    [[ "$CLEAN_HASH" =~ ^[0-9a-f]+$ ]] || die "reset: CLEAN_HASH contains invalid characters"

    require_cmd git

    log "Resetting workspace to $CLEAN_HASH ..."
    git -C "$workspace" reset --hard "$CLEAN_HASH"
    git -C "$workspace" clean -fdx

    # Verify
    local current_hash; current_hash="$(git -C "$workspace" rev-parse HEAD)"
    if [[ "$current_hash" != "$CLEAN_HASH" ]]; then
        die "reset: HEAD ($current_hash) does not match expected ($CLEAN_HASH)"
    fi

    log "Workspace reset to clean state."
}

# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

cmd_apply() {
    local workspace="" patch_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --workspace) [[ $# -ge 2 ]] || die "apply: $1 requires a value"; workspace="$2"; shift 2 ;;
            --patch)     [[ $# -ge 2 ]] || die "apply: $1 requires a value"; patch_file="$2"; shift 2 ;;
            *) die "apply: unknown option '$1'" ;;
        esac
    done

    [[ -n "$workspace" ]]  || die "apply: --workspace is required"
    [[ -d "$workspace" ]]  || die "apply: workspace directory not found: $workspace"
    [[ -n "$patch_file" ]] || die "apply: --patch is required"
    [[ -f "$patch_file" ]] || die "apply: patch file not found: $patch_file"

    require_cmd git

    # Convert patch path to absolute so it works from inside workspace
    local abs_patch; abs_patch="$(cd "$(dirname "$patch_file")" && pwd)/$(basename "$patch_file")"

    log "Applying patch $patch_file ..."

    # Try git apply first
    if git -C "$workspace" apply --check "$abs_patch" 2>/dev/null; then
        git -C "$workspace" apply "$abs_patch"
        log "Patch applied via git apply."
    else
        log "git apply --check failed, falling back to patch -p1 ..."
        require_cmd patch
        (cd "$workspace" && patch -p1 < "$abs_patch")
        log "Patch applied via patch -p1."
    fi

    # Ensure git user is configured (may not be set in CI environments)
    git -C "$workspace" config user.name  "${GIT_AUTHOR_NAME:-agent-eval}"
    git -C "$workspace" config user.email "${GIT_AUTHOR_EMAIL:-agent-eval@noreply}"
    # Commit the applied changes for clean diffing
    git -C "$workspace" add -A
    git -C "$workspace" commit -m "applied patch" --allow-empty

    log "Patch committed."
}

# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

cmd_cleanup() {
    local workspace=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --workspace) [[ $# -ge 2 ]] || die "cleanup: $1 requires a value"; workspace="$2"; shift 2 ;;
            *) die "cleanup: unknown option '$1'" ;;
        esac
    done

    [[ -n "$workspace" ]] || die "cleanup: --workspace is required"

    local meta; meta="$(meta_dir_for "$workspace")"

    log "Removing workspace: $workspace"
    force_rm "$workspace"

    log "Removing metadata: $meta"
    force_rm "$meta"

    log "Cleanup complete."
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    cat <<'USAGE'
Usage: reset_workspace.sh <command> [options]

Commands:
  prepare   --repo-url URL --base-commit SHA --workspace DIR --ground-truth FILE
            Clone repo at base commit, sanitize history, store metadata.

  reset     --workspace DIR
            Reset workspace to the clean base-commit state.

  apply     --workspace DIR --patch FILE
            Apply a patch file to the workspace.

  cleanup   --workspace DIR
            Remove workspace and metadata directories.
USAGE
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

command="$1"; shift

case "$command" in
    prepare)  cmd_prepare  "$@" ;;
    reset)    cmd_reset    "$@" ;;
    apply)    cmd_apply    "$@" ;;
    cleanup)  cmd_cleanup  "$@" ;;
    -h|--help|help) usage ;;
    *) die "Unknown command: $command. Run with --help for usage." ;;
esac
