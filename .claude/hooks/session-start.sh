#!/bin/bash
set -euo pipefail

# Only run in Claude Code on the web (remote) sessions; skip on local machines.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Create an isolated virtualenv (idempotent) and install the package with dev deps.
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[dev]"

# Persist the venv for the rest of the session so python/pytest use it automatically.
{
  echo "export VIRTUAL_ENV=\"$CLAUDE_PROJECT_DIR/.venv\""
  echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\""
} >> "$CLAUDE_ENV_FILE"

echo "session-start hook: dependencies installed."
