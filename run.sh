#!/bin/zsh
emulate -L zsh
set -e
set -u
set -o pipefail

# Runs in place from the repo checkout; TASK_DIR follows this script's location.
TASK_DIR="${0:A:h}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
ENV_FILE="${HERMES_ENV_FILE:-$HERMES_HOME/.env}"
LOG_DIR="$TASK_DIR/logs"
PYTHON="${HERMES_WORKFLOW_MCP_PYTHON:-$HERMES_HOME/hermes-agent/venv/bin/python}"
HOST="${HERMES_WORKFLOW_MCP_HOST:-127.0.0.1}"
PORT="${HERMES_WORKFLOW_MCP_PORT:-8813}"

mkdir -p "$LOG_DIR"

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { print -r -- "$(ts) $*"; }

if [[ -x /usr/libexec/path_helper ]]; then
  eval "$(/usr/libexec/path_helper -s)"
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  if ! source "$ENV_FILE"; then
    log "Failed to source env file: $ENV_FILE"
    exit 78
  fi
  set +a
else
  log "Env file not found: $ENV_FILE"
fi

if [[ ! -x "$PYTHON" ]]; then
  log "Python executable not found or not executable: $PYTHON"
  log "Install an MCP-capable Python venv or set HERMES_WORKFLOW_MCP_PYTHON"
  exit 127
fi

export HERMES_WORKFLOW_MCP_HOST="$HOST"
export HERMES_WORKFLOW_MCP_PORT="$PORT"
export OBSIDIAN_VAULT_PATH="${OBSIDIAN_VAULT_PATH:-$HOME/Obsidian/Work}"
export PYTHONUNBUFFERED=1

log "Starting Workflow MCP host=$HOST port=$PORT python=$PYTHON"
exec "$PYTHON" "$TASK_DIR/server.py"
