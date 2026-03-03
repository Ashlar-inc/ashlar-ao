"""
Ashlr AO — Constants, Logging, and Utilities

Logging setup, ANSI stripping, secret redaction, banner, and dependency checks.
"""

import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


# ── Directory Setup & Migration ──

ASHLR_DIR = Path.home() / ".ashlr"

_LEGACY_DIR = Path.home() / ".ashlar"
if _LEGACY_DIR.is_dir() and not ASHLR_DIR.exists():
    _LEGACY_DIR.rename(ASHLR_DIR)
    for _old_name, _new_name in [
        ("ashlar.yaml", "ashlr.yaml"),
        ("ashlar.db", "ashlr.db"),
        ("ashlar.log", "ashlr.log"),
    ]:
        _old = ASHLR_DIR / _old_name
        if _old.exists():
            _old.rename(ASHLR_DIR / _new_name)

ASHLR_DIR.mkdir(exist_ok=True)

# ── Logging ──

LOG_COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
}
RESET = "\033[0m"


class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = LOG_COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname:<8}{RESET}"
        return super().format(record)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler with colors
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(ColoredFormatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(ch)

    # File handler with rotation (10 MB max, 5 backups)
    fh = logging.handlers.RotatingFileHandler(
        ASHLR_DIR / "ashlr.log", maxBytes=10 * 1024 * 1024, backupCount=5
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(fh)


log = logging.getLogger("ashlr")

# ── ANSI Stripping ──

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE_RE.sub('', text)


# ── Secret Redaction ──

_SECRET_PATTERNS = [
    re.compile(r'\b(sk-[a-zA-Z0-9]{20,})'),           # OpenAI/Anthropic API keys
    re.compile(r'\b(ghp_[a-zA-Z0-9]{36,})'),           # GitHub PATs (classic)
    re.compile(r'\b(github_pat_[a-zA-Z0-9_]{22,})'),   # GitHub PATs (fine-grained)
    re.compile(r'\b(gho_[a-zA-Z0-9]{36,})'),           # GitHub OAuth tokens
    re.compile(r'\b(ghs_[a-zA-Z0-9]{36,})'),           # GitHub App installation tokens
    re.compile(r'\b(xai-[a-zA-Z0-9]{20,})'),           # xAI API keys
    re.compile(r'\b(AKIA[A-Z0-9]{16})'),               # AWS access keys
    re.compile(r'\b(xoxb-[a-zA-Z0-9\-]{20,})'),       # Slack bot tokens
    re.compile(r'\b(xoxp-[a-zA-Z0-9\-]{20,})'),       # Slack user tokens
    re.compile(r'\b(xoxs-[a-zA-Z0-9\-]{20,})'),       # Slack session tokens
    re.compile(r'\b(SG\.[a-zA-Z0-9_\-]{22,}\.[a-zA-Z0-9_\-]{22,})'),  # SendGrid API keys
    re.compile(r'\b(np_[a-zA-Z0-9]{20,})'),            # npm tokens
    re.compile(r'\b(pypi-[a-zA-Z0-9]{20,})'),          # PyPI tokens
    re.compile(r'\b(Bearer\s+[a-zA-Z0-9\-._~+/]{20,})'),  # Bearer tokens
    re.compile(r'(?i)\bpassword\s*[=:]\s*\S+'),        # password= fields
    re.compile(r'(?i)\bsecret\s*[=:]\s*\S+'),          # secret= fields
    re.compile(r'(?i)\bapi[_-]?key\s*[=:]\s*\S+'),     # api_key= fields
    re.compile(r'(?i)\btoken\s*[=:]\s*["\']?[a-zA-Z0-9\-._]{20,}'),  # token= fields
    re.compile(r'\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}'),  # JWT tokens
    re.compile(r'(?i)\b(mongodb(\+srv)?://[^\s]+)'),   # MongoDB connection strings
    re.compile(r'(?i)\b(postgres(ql)?://[^\s]+)'),     # PostgreSQL connection strings
    re.compile(r'(?i)\b(mysql://[^\s]+)'),             # MySQL connection strings
    re.compile(r'(?i)\b(redis://[^\s]+)'),             # Redis connection strings
]


def redact_secrets(text: str) -> str:
    """Replace secret patterns with redacted placeholders."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub('****[REDACTED]', result)
    return result


def print_banner() -> None:
    from ashlr_ao import __version__
    print("\n\033[36m", end="")
    print("  ╔═══════════════════════════════════╗")
    print("  ║          A S H L R   A O         ║")
    print("  ║     Agent Orchestration Platform   ║")
    print("  ╚═══════════════════════════════════╝")
    print(f"  v{__version__}\033[0m")


def check_dependencies() -> bool:
    """Check for required (tmux) and optional (claude) dependencies.
    Returns True if claude CLI is found, False for demo mode."""
    if not shutil.which("tmux"):
        log.critical("tmux is required but not found. Install: brew install tmux")
        sys.exit(1)
    log.info("tmux found")

    if shutil.which("claude") and not os.environ.get("CLAUDECODE"):
        try:
            result = subprocess.run(
                ["claude", "--version"], capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                version = lines[0][:80] if lines else "unknown"
                log.info(f"claude CLI validated: {version}")
                return True
            else:
                log.warning(f"claude CLI found but --version failed (exit {result.returncode}) — using demo mode")
                return False
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning(f"claude CLI found but not functional ({e}) — using demo mode")
            return False
    elif os.environ.get("CLAUDECODE"):
        log.warning("Running inside Claude Code session — using demo mode to avoid nested sessions")
        return False
    else:
        log.warning("claude CLI not found — agents will run in demo mode (bash)")
        return False
