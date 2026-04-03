#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

log() {
  printf '[pre-push] %s\n' "$*"
}

fail() {
  printf '[pre-push] ERROR: %s\n' "$*" >&2
  exit 1
}

detect_python() {
  for candidate in \
    "$REPO_ROOT/.venv-wsl/bin/python" \
    "$REPO_ROOT/.venv/bin/python" \
    "$REPO_ROOT/.venv/Scripts/python.exe"
  do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}

ensure_node_18() {
  NODE_BIN=""
  NODE_VERSION=""

  if command -v node >/dev/null 2>&1; then
    NODE_BIN=$(command -v node)
    NODE_VERSION=$("$NODE_BIN" -v 2>/dev/null || true)
  fi

  NODE_MAJOR=$(printf '%s' "$NODE_VERSION" | sed -E 's/^v([0-9]+).*/\1/')
  case "$NODE_MAJOR" in
    ''|*[!0-9]*)
      NODE_MAJOR=0
      ;;
  esac

  if [ "$NODE_MAJOR" -lt 18 ]; then
    for candidate in "$HOME"/.nvm/versions/node/*/bin/node; do
      [ -x "$candidate" ] || continue
      CANDIDATE_VERSION=$("$candidate" -v 2>/dev/null || true)
      CANDIDATE_MAJOR=$(printf '%s' "$CANDIDATE_VERSION" | sed -E 's/^v([0-9]+).*/\1/')
      case "$CANDIDATE_MAJOR" in
        ''|*[!0-9]*)
          continue
          ;;
      esac
      if [ "$CANDIDATE_MAJOR" -ge 18 ]; then
        NODE_BIN="$candidate"
        NODE_VERSION="$CANDIDATE_VERSION"
        NODE_MAJOR="$CANDIDATE_MAJOR"
        break
      fi
    done
  fi

  [ -n "$NODE_BIN" ] || fail "Node.js is unavailable. Install Node 18+ before pushing."

  if [ "$NODE_MAJOR" -lt 18 ]; then
    fail "Node.js 18+ is required. Current version: $NODE_VERSION"
  fi

  export NODE_BIN
  log "Using Node: $NODE_VERSION ($NODE_BIN)"
}

run_python() {
  log "Running: $PYTHON $*"
  "$PYTHON" "$@"
}

run_shell() {
  log "Running: $*"
  "$@"
}

PYTHON=$(detect_python) || fail "No Python interpreter found. Create .venv-wsl/.venv or install python3."
log "Using Python: $PYTHON"

if [ -f "$REPO_ROOT/.pre-commit-config.yaml" ]; then
  export PRE_COMMIT_HOME="$REPO_ROOT/.tmp/pre-commit"
  mkdir -p "$PRE_COMMIT_HOME"
  if "$PYTHON" -m pre_commit --version >/dev/null 2>&1; then
    run_python -m pre_commit run detect-secrets --all-files
    run_python -m pre_commit run --all-files --show-diff-on-failure
  else
    fail ".pre-commit-config.yaml exists, but pre-commit is unavailable in the selected Python environment."
  fi
else
  log "Skipping pre-commit checks (.pre-commit-config.yaml is missing in this repo)."
fi

run_python -m compileall videohelpersuite __init__.py scripts tests
run_python scripts/run_unittests.py

ensure_node_18
if [ -f "$REPO_ROOT/web/js/VHS.core.js" ]; then
  log "Running: $NODE_BIN --input-type=module --check < web/js/VHS.core.js"
  "$NODE_BIN" --input-type=module --check < "$REPO_ROOT/web/js/VHS.core.js"
fi

run_shell git diff --check

log "Pre-push checks passed."
