#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# Ashlar AO — Launch Script
# ──────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PORT="${ASHLAR_PORT:-5000}"

echo ""
echo "  ╔═══════════════════════════════════╗"
echo "  ║         A S H L A R   A O        ║"
echo "  ║     Agent Orchestration Platform   ║"
echo "  ╚═══════════════════════════════════╝"
echo ""

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 is required. Install it: brew install python3"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_MINOR" -lt 11 ]; then
    echo "❌ Python 3.11+ required (found $PYTHON_VERSION)"
    exit 1
fi
echo "✓ Python $PYTHON_VERSION"

# 2. Check tmux
if ! command -v tmux &>/dev/null; then
    echo "❌ tmux is required. Install it:"
    echo "   macOS:  brew install tmux"
    echo "   Linux:  sudo apt install tmux"
    exit 1
fi
echo "✓ tmux $(tmux -V | cut -d' ' -f2)"

# 3. Check claude CLI (warn but don't fail)
if command -v claude &>/dev/null; then
    echo "✓ claude CLI found"
else
    echo "⚠ claude CLI not found — agents will run in demo mode"
    echo "  Install: npm install -g @anthropic-ai/claude-code"
fi

# 4. Check port availability
if lsof -i ":$PORT" -sTCP:LISTEN &>/dev/null; then
    echo ""
    echo "⚠ Port $PORT is in use."
    # Check if it's AirPlay on macOS
    if [ "$(uname)" = "Darwin" ] && lsof -i ":$PORT" -sTCP:LISTEN 2>/dev/null | grep -q "ControlCe"; then
        echo "  This is likely macOS AirPlay Receiver."
        echo "  → System Settings > General > AirDrop & Handoff > AirPlay Receiver → off"
    else
        PROC=$(lsof -i ":$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
        PNAME=$(ps -p "$PROC" -o comm= 2>/dev/null || echo "unknown")
        echo "  Process: $PNAME (PID $PROC)"
    fi
    echo "  Set ASHLAR_PORT=8080 to use a different port."
    echo ""
    exit 1
fi

# 5. Clean stale Ashlar tmux sessions from previous crashes
STALE_SESSIONS=$(tmux list-sessions 2>/dev/null | grep "^ashlar-" | cut -d: -f1 || true)
if [ -n "$STALE_SESSIONS" ]; then
    echo "→ Cleaning $(echo "$STALE_SESSIONS" | wc -l | tr -d ' ') stale tmux sessions..."
    echo "$STALE_SESSIONS" | while read -r sess; do
        tmux kill-session -t "$sess" 2>/dev/null || true
    done
fi

# 6. Create virtual environment if needed
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "→ Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# 7. Activate venv and install deps
source "$VENV_DIR/bin/activate"

echo "→ Installing dependencies..."
pip install -q -r "$SCRIPT_DIR/requirements.txt"

# 8. Create config directory
mkdir -p "$HOME/.ashlar"

# 9. Show optional env vars
if [ -n "${XAI_API_KEY:-}" ]; then
    echo "✓ XAI_API_KEY set — LLM summaries enabled"
else
    echo "  (Optional: set XAI_API_KEY for LLM-powered agent summaries)"
fi

# 10. Launch server
echo ""
echo "→ Starting Ashlar server..."
echo "  Dashboard: http://127.0.0.1:$PORT"
echo "  Press Ctrl+C to stop"
echo ""

python3 "$SCRIPT_DIR/ashlar_server.py" "$@"
