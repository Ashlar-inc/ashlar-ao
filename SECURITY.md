# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.6.x   | Yes       |
| 1.5.x   | Security fixes only |
| < 1.5   | No        |

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Email security reports to the maintainers with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide a timeline for a fix.

## Security Model

Ashlr AO is designed as a **local-first, single-machine** orchestration tool. The default threat model assumes a trusted local user.

### Defenses in Place

- **Path restriction** — working directories limited to `~/` and `/tmp`, with symlink resolution (`os.path.realpath`) to prevent traversal
- **Input validation** — message size limits (50K chars), request body limits, tool name validation (alphanumeric/hyphen/underscore)
- **Rate limiting** — per-IP request throttling, auto-approve rate limit (5/min/agent)
- **Auth** — bcrypt password hashing, HttpOnly/SameSite=Strict/Secure session cookies, HMAC-based bearer token comparison
- **Security headers** — CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy
- **Secret redaction** — API keys and tokens detected and redacted in captured output
- **Ownership enforcement** — all mutation endpoints verify agent ownership (owner or admin)
- **License validation** — Ed25519 signature verification, offline-only (no phone-home)

### Known Limitations

1. **Default bind is localhost** — safe for local use. For remote access, deploy behind a reverse proxy (Caddy/nginx) with HTTPS.
2. **`--dangerously-skip-permissions`** — Claude Code default. Use `--permission-mode plan` for safer operation.
3. **File conflict detection only** — warns when two agents edit the same file but does not prevent it.
4. **SQLite is single-writer** — not suitable for high-concurrency multi-server deployments.
