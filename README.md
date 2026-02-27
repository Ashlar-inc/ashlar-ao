# Ashlar AO

A local-first agent orchestration platform. Manage multiple AI coding agents (Claude Code, Codex, etc.) across projects from a single browser-based command center.

Spawn agents, monitor their status in real time, approve plans, respond to questions, and coordinate work — all without switching terminals.

## Prerequisites

- **Python 3.11+**
- **tmux** — used for agent process isolation and output capture
- At least one agent backend CLI:
  - [Claude Code](https://www.npmjs.com/package/@anthropic-ai/claude-code): `npm i -g @anthropic-ai/claude-code`
  - [Codex](https://www.npmjs.com/package/@openai/codex): `npm i -g @openai/codex`

If no backend is installed, agents run in demo mode.

### Install tmux

```bash
# macOS
brew install tmux

# Linux
sudo apt install tmux
```

## Quick Start

```bash
./start.sh
```

The launch script handles everything: checks dependencies, creates a Python virtual environment, installs packages, and starts the server.

Open **http://127.0.0.1:5000** in your browser.

### Port Conflicts

Port 5000 conflicts with macOS AirPlay Receiver. Either disable AirPlay Receiver in System Settings, or set a different port:

```bash
ASHLAR_PORT=8080 ./start.sh
```

## Configuration

Config lives at `~/.ashlar/ashlar.yaml` (auto-created on first run). Defaults work out of the box.

### Optional Environment Variables

| Variable | Purpose |
|----------|---------|
| `ASHLAR_PORT` | Override server port (default: 5000) |
| `XAI_API_KEY` | Enable LLM-powered agent summaries via xAI Grok |

## Architecture

Two files make up the entire application:

- **`ashlar_server.py`** — Python aiohttp server. Manages agents via tmux, serves the dashboard, provides REST + WebSocket APIs, collects system metrics.
- **`ashlar_dashboard.html`** — Single HTML file with all CSS and JS inline. No build step, no bundler, no node_modules.

Data is persisted in SQLite at `~/.ashlar/ashlar.db`.

## Usage

- **Spawn agents** from the dashboard or command palette (`Cmd+K`)
- **Monitor status** via live-updating cards (planning, working, waiting, error)
- **Respond to agents** inline when they need input
- **Push-to-talk** with `Space` for voice commands
- **Keyboard shortcuts**: `Cmd+N` new agent, `1-9` focus agent, `Cmd+Shift+A` approve

## Development

See `CLAUDE.md` for full architecture, data models, API reference, and implementation details.
