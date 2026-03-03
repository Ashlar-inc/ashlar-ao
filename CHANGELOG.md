# Changelog

## 1.5.0 — 2026-03-03

- **Server modularization**: Split 10.8K-line `server.py` into 16 focused modules (`models.py`, `config.py`, `database.py`, `manager.py`, `websocket.py`, `background.py`, `middleware.py`, `intelligence.py`, `auth.py`, `handlers/`, etc.) — `server.py` now ~3.9K lines as re-export hub
- **Security fixes**: WebSocket ownership bypass, config import RCE allowlist, dashboard XSS via innerHTML, clone agent license bypass, bearer token timing attack (hmac.compare_digest), missing ownership check on inter-agent messages
- **Bug fixes**: Agent `cpu_pct` serialization, `_safe_eval_condition` operator detection, path traversal boundary check, WebSocket sync_request missing fields, `_safe_commit` in background tasks, dashboard card double-handler, undefined CSS variable, native `confirm()` replaced with custom modal, dead code cleanup
- **Performance**: bcrypt moved to thread pool, global search to thread pool, synchronous file writes to thread pool, collaboration graph O(N²) → O(1) edge updates, dashboard visibility-change pause for intervals
- **Infrastructure**: Replaced abandoned `aiohttp-cors` with native middleware, `.dockerignore` added, `asyncio_mode = "auto"` in pytest config, `bcrypt` and `PyJWT[crypto]` in requirements.txt
- **Test improvements**: Split `test_lifecycle.py` (2322 → 1552 lines), created `test_intelligence.py`, new tests for health_check_loop, metrics_loop, memory_watchdog_loop, archive_cleanup_loop, IntelligenceClient HTTP interaction
- **Backward compatibility**: `ashlr_server.py` shim fully preserved — all existing imports and test patches continue to work
- 1319 tests across 19 test files

## 1.4.0 — 2026-03-03

- Open-core monetization: Community (free, 5 agents) and Pro (paid, 100 agents) tiers
- Ed25519-signed JWT licensing — fully offline, no phone-home
- License API: `GET/POST/DELETE /api/license/{status,activate,deactivate}`
- Feature gating: intelligence, workflows, fleet presets, multi-user behind Pro
- Dashboard: plan badge in header, license settings section, upgrade prompts on 403
- Admin-only config updates when auth is enabled, max_agents clamped to license ceiling
- `generate_license.py` standalone tool for keypair generation and license signing
- Added PyJWT[crypto] runtime dependency, pytest-aiohttp dev dependency
- ~40 new licensing tests in `tests/test_licensing.py`

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
