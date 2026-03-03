# Changelog

## 1.3.1 — 2026-03-03

- Docker: run as non-root user, pin Claude CLI to major version
- Docker Compose: enable auth and set CORS origin by default
- Caddyfile: add HSTS header
- Server: support `ASHLR_REQUIRE_AUTH` env var to force auth on
- Server: truncate auto-generated auth token in logs
- Server: disable CORS credentials when origin is wildcard
- CI: bump coverage threshold to 60%, add pip cache
- Fix GitHub clone URL in README

## 1.3.0 — 2026-03-03

- Production hardening: security headers, ownership checks, rate limiting
- ARIA accessibility improvements across dashboard
- Silent exception logging (16 handlers)
- Archive cleanup background task (48hr retention)
- Enhanced request logging (user_id, body_size, ms)
- Config validators (log_level, host, alert_patterns)
- 1195 tests

## 1.2.0 — 2026-03-02

- Session resume UI in spawn dialog
- Git branch tracking per agent with dashboard badge and filter
- Project UPDATE endpoint (`PUT /api/projects/{id}`)
- DB migrations for model, tools_allowed, git_branch columns
- 1106 tests

## 1.1.1 — 2026-03-02

- Exception logging and auth rate limits
- CI linting and coverage checks

## 1.1.0 — 2026-03-02

- Rename Ashlar to Ashlr with migration support
- CLI args: `--port`, `--host`, `--demo`, `--log-level`, `--version`
- GitHub Actions CI (Python 3.11-3.13) and PyPI publish workflow
- 987 tests

## 1.0.0 — 2026-03-01

- Initial release as pip-installable package (`ashlr-ao`)
- Agent orchestration via tmux with real-time dashboard
- REST + WebSocket APIs, SQLite persistence
- Multi-user auth with session cookies and org model
- Intelligence layer via xAI Grok (summaries, NLU, fleet analysis)
- Docker + Caddy deployment with auto-HTTPS
