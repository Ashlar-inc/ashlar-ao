#!/usr/bin/env python3
"""
Ashlar AO — Agent Orchestration Server

Single-file aiohttp server that manages AI coding agents via tmux,
serves the web dashboard, and provides REST + WebSocket APIs.
"""

# ─────────────────────────────────────────────
# Section 1: Imports, Logging, Banner
# ─────────────────────────────────────────────

import asyncio
import collections
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import aiosqlite
from aiohttp import web
import aiohttp_cors
import psutil
import yaml

# ── Logging ──

ASHLAR_DIR = Path.home() / ".ashlar"
ASHLAR_DIR.mkdir(exist_ok=True)

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

    # File handler
    fh = logging.FileHandler(ASHLAR_DIR / "ashlar.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(fh)


log = logging.getLogger("ashlar")

# ── Module-level ANSI stripping utility ──

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE_RE.sub('', text)


def print_banner() -> None:
    print("\n\033[36m", end="")
    print("  ╔═══════════════════════════════════╗")
    print("  ║         A S H L A R   A O        ║")
    print("  ║     Agent Orchestration Platform   ║")
    print("  ╚═══════════════════════════════════╝")
    print(f"\033[0m")


def check_dependencies() -> bool:
    """Check for required (tmux) and optional (claude) dependencies.
    Returns True if claude CLI is found, False for demo mode."""
    if not shutil.which("tmux"):
        log.critical("tmux is required but not found. Install: brew install tmux")
        sys.exit(1)
    log.info("tmux found")

    if shutil.which("claude") and not os.environ.get("CLAUDECODE"):
        log.info("claude CLI found")
        return True
    elif os.environ.get("CLAUDECODE"):
        log.warning("Running inside Claude Code session — using demo mode to avoid nested sessions")
        return False
    else:
        log.warning("claude CLI not found — agents will run in demo mode (bash)")
        return False


# ─────────────────────────────────────────────
# Section 2: Configuration
# ─────────────────────────────────────────────

DEFAULT_CONFIG = {
    "server": {"host": "127.0.0.1", "port": 5000, "log_level": "INFO"},
    "agents": {
        "max_concurrent": 16,
        "default_role": "general",
        "default_working_dir": "~/Projects",
        "output_capture_interval_sec": 1.0,
        "memory_limit_mb": 2048,
        "default_backend": "claude-code",
        "backends": {
            "claude-code": {"command": "claude", "args": ["--dangerously-skip-permissions"]},
            "codex": {"command": "codex", "args": []},
        },
    },
    "voice": {"enabled": True, "ptt_key": "Space", "feedback_sounds": True},
    "display": {"theme": "dark", "cards_per_row": 4},
    "llm": {
        "enabled": False,
        "provider": "xai",
        "model": "grok-4-1-fast-reasoning",
        "api_key_env": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "summary_interval_sec": 10.0,
        "max_output_lines": 30,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 5000
    log_level: str = "INFO"
    max_agents: int = 16
    default_role: str = "general"
    default_working_dir: str = "~/Projects"
    output_capture_interval: float = 1.0
    memory_limit_mb: int = 2048
    claude_command: str = "claude"
    claude_args: list = field(default_factory=lambda: ["--dangerously-skip-permissions"])
    demo_mode: bool = False
    # Multi-backend support
    backends: dict = field(default_factory=lambda: {
        "claude-code": {"command": "claude", "args": ["--dangerously-skip-permissions"]},
        "codex": {"command": "codex", "args": []},
    })
    default_backend: str = "claude-code"
    # Idle agent reaping
    idle_agent_ttl: int = 3600  # seconds before idle/complete agents are reaped
    # LLM summary config
    llm_enabled: bool = False
    llm_provider: str = "xai"
    llm_model: str = "grok-4-1-fast-reasoning"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.x.ai/v1"
    llm_summary_interval: float = 10.0
    llm_max_output_lines: int = 30

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "max_agents": self.max_agents,
            "default_role": self.default_role,
            "default_working_dir": self.default_working_dir,
            "output_capture_interval": self.output_capture_interval,
            "memory_limit_mb": self.memory_limit_mb,
            "demo_mode": self.demo_mode,
            "default_backend": self.default_backend,
            "backends": {k: {"command": v.get("command", ""), "available": bool(shutil.which(v.get("command", "")))} for k, v in self.backends.items()},
            "llm_enabled": self.llm_enabled,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_summary_interval": self.llm_summary_interval,
        }


def load_config(has_claude: bool = True) -> Config:
    config_dir = ASHLAR_DIR
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "ashlar.yaml"

    # Also check for config in the project directory
    local_config = Path(__file__).parent / "ashlar.yaml"

    raw = DEFAULT_CONFIG.copy()

    if config_path.exists():
        try:
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            raw = deep_merge(raw, user_config)
        except Exception as e:
            log.warning(f"Failed to load config from {config_path}: {e}")
    elif local_config.exists():
        # Copy local config to ~/.ashlar/ on first run
        try:
            shutil.copy2(local_config, config_path)
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            raw = deep_merge(raw, user_config)
            log.info(f"Copied config to {config_path}")
        except Exception as e:
            log.warning(f"Failed to copy config: {e}")
    else:
        # Write defaults
        try:
            with open(config_path, "w") as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
            log.info(f"Created default config at {config_path}")
        except Exception as e:
            log.warning(f"Failed to write default config: {e}")

    server = raw.get("server", {})
    agents = raw.get("agents", {})
    backends = agents.get("backends", {})
    claude_backend = backends.get("claude-code", {})
    llm = raw.get("llm", {})

    default_wd = agents.get("default_working_dir", "~/Projects")
    default_wd = os.path.expanduser(default_wd)

    # Resolve LLM API key from env var
    api_key_env = llm.get("api_key_env", "XAI_API_KEY")
    llm_api_key = os.environ.get(api_key_env, "")

    return Config(
        host=server.get("host", "127.0.0.1"),
        port=server.get("port", 5000),
        log_level=server.get("log_level", "INFO"),
        max_agents=agents.get("max_concurrent", 16),
        default_role=agents.get("default_role", "general"),
        default_working_dir=default_wd,
        output_capture_interval=agents.get("output_capture_interval_sec", 1.0),
        memory_limit_mb=agents.get("memory_limit_mb", 2048),
        claude_command=claude_backend.get("command", "claude"),
        claude_args=claude_backend.get("args", ["--dangerously-skip-permissions"]),
        demo_mode=not has_claude,
        backends=backends or DEFAULT_CONFIG["agents"]["backends"],
        default_backend=agents.get("default_backend", "claude-code"),
        llm_enabled=llm.get("enabled", False) and bool(llm_api_key),
        llm_provider=llm.get("provider", "xai"),
        llm_model=llm.get("model", "grok-4-1-fast-reasoning"),
        llm_api_key=llm_api_key,
        llm_base_url=llm.get("base_url", "https://api.x.ai/v1"),
        llm_summary_interval=llm.get("summary_interval_sec", 10.0),
        llm_max_output_lines=llm.get("max_output_lines", 30),
    )


# ─────────────────────────────────────────────
# Section 3: Data Models
# ─────────────────────────────────────────────

@dataclass
class Role:
    key: str
    name: str
    icon: str
    color: str
    description: str
    system_prompt: str
    max_memory_mb: int = 2048

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "icon": self.icon,
            "color": self.color,
            "description": self.description,
            "system_prompt": self.system_prompt,
        }


BUILTIN_ROLES: dict[str, Role] = {
    "frontend": Role(
        key="frontend", name="Frontend Engineer", icon="🎨", color="#8B5CF6",
        description="React, Vue, CSS, UI/UX, accessibility",
        system_prompt="You are a frontend specialist. Focus on component architecture, responsive design, accessibility (WCAG), and performance. Prefer TypeScript, functional components, and Tailwind. Write tests for all components.",
    ),
    "backend": Role(
        key="backend", name="Backend Engineer", icon="⚙", color="#3B82F6",
        description="APIs, databases, Python, Node.js, auth",
        system_prompt="You are a backend specialist. Focus on API design, database schemas, auth, and error handling. Write clean, well-tested code with proper validation and logging. Prefer async patterns.",
    ),
    "devops": Role(
        key="devops", name="DevOps Engineer", icon="🚀", color="#F97316",
        description="Infrastructure, CI/CD, Docker, deployment",
        system_prompt="You are a DevOps specialist. Focus on infrastructure as code, CI/CD pipelines, containerization, monitoring, and deployment automation. Prioritize reliability and observability.",
    ),
    "tester": Role(
        key="tester", name="QA Engineer", icon="🧪", color="#22C55E",
        description="Unit tests, integration tests, E2E, coverage",
        system_prompt="You are a QA specialist. Write comprehensive tests: unit, integration, and E2E. Aim for high coverage on critical paths. Test edge cases, error conditions, and race conditions.",
    ),
    "reviewer": Role(
        key="reviewer", name="Code Reviewer", icon="👁", color="#EAB308",
        description="Code review, best practices, architecture",
        system_prompt="You are a code reviewer. Audit code for bugs, security issues, performance problems, and maintainability. Be thorough but constructive. Suggest specific improvements with code examples.",
    ),
    "security": Role(
        key="security", name="Security Auditor", icon="🔒", color="#EF4444",
        description="Vulnerability audit, dependency scanning, hardening",
        system_prompt="You are a security specialist. Audit for vulnerabilities: injection, XSS, CSRF, auth bypass, secrets exposure, dependency CVEs. Provide severity ratings and specific fix recommendations.",
    ),
    "architect": Role(
        key="architect", name="Architect", icon="🏗", color="#06B6D4",
        description="System design, planning, technical decisions",
        system_prompt="You are a systems architect. Focus on high-level design, component boundaries, data flow, scalability, and technical tradeoffs. Create clear plans before implementation. Document decisions.",
    ),
    "docs": Role(
        key="docs", name="Documentation", icon="📝", color="#A855F7",
        description="READMEs, API docs, inline comments, guides",
        system_prompt="You are a documentation specialist. Write clear, concise docs: READMEs, API references, inline comments, architecture guides. Focus on the 'why' not just the 'what'. Include examples.",
    ),
    "general": Role(
        key="general", name="General", icon="🤖", color="#64748B",
        description="All-purpose agent, no specialization",
        system_prompt="You are a skilled software engineer. Approach tasks methodically: understand the requirement, explore the codebase, plan your approach, implement, and verify. Ask clarifying questions when needed.",
    ),
}


@dataclass
class Agent:
    id: str
    name: str
    role: str
    status: str  # spawning|planning|reading|working|waiting|idle|error|paused
    working_dir: str
    backend: str
    task: str
    summary: str = ""
    context_pct: float = 0.0
    memory_mb: float = 0.0
    needs_input: bool = False
    input_prompt: str | None = None
    error_message: str | None = None
    project_id: str | None = None
    tmux_session: str = ""
    pid: int | None = None
    created_at: str = ""
    updated_at: str = ""
    script_path: str | None = None
    related_agents: list = field(default_factory=list)
    progress_pct: float = 0.0
    phase: str = ""
    # Auto-restart fields
    restart_count: int = 0
    max_restarts: int = 3
    last_restart_time: float = 0.0
    restarted_at: str = ""
    _restart_in_progress: bool = field(default=False, repr=False)
    # Health scoring fields
    health_score: float = 1.0
    error_count: int = 0
    last_output_time: float = 0.0
    # Per-agent metrics
    time_to_first_output: float = 0.0  # seconds from spawn to first output
    total_output_lines: int = 0
    output_rate: float = 0.0  # lines per minute, rolling average
    _phase: str = field(default="unknown", repr=False)
    output_lines: collections.deque = field(default_factory=lambda: collections.deque(maxlen=500))
    _prev_output_hash: int = field(default=0, repr=False)
    _total_chars: int = field(default=0, repr=False)
    _spawn_time: float = field(default=0.0, repr=False)
    _last_needs_input_event: float = field(default=0.0, repr=False)
    _last_llm_summary_time: float = field(default=0.0, repr=False)
    _llm_summary: str = field(default="", repr=False)
    unread_messages: int = field(default=0, repr=False)
    _first_output_received: bool = field(default=False, repr=False)
    _output_line_timestamps: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=60), repr=False
    )  # timestamps of recent output batches for rate calculation
    _error_entered_at: float = field(default=0.0, repr=False)  # monotonic time when error status was first detected

    def to_dict(self) -> dict:
        role_obj = BUILTIN_ROLES.get(self.role)
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "role_icon": role_obj.icon if role_obj else "🤖",
            "role_color": role_obj.color if role_obj else "#64748B",
            "status": self.status,
            "working_dir": self.working_dir,
            "backend": self.backend,
            "task": self.task,
            "summary": self.summary,
            "context_pct": self.context_pct,
            "memory_mb": self.memory_mb,
            "needs_input": self.needs_input,
            "input_prompt": self.input_prompt,
            "error_message": self.error_message,
            "project_id": self.project_id,
            "pid": self.pid,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress_pct": self.progress_pct,
            "phase": self.phase,
            "related_agents": self.related_agents,
            "unread_messages": self.unread_messages,
            "restart_count": self.restart_count,
            "max_restarts": self.max_restarts,
            "restarted_at": self.restarted_at,
            "health_score": round(self.health_score, 3),
            "error_count": self.error_count,
            "time_to_first_output": round(self.time_to_first_output, 2),
            "total_output_lines": self.total_output_lines,
            "output_rate": round(self.output_rate, 1),
        }

    def to_dict_full(self) -> dict:
        d = self.to_dict()
        d["output_lines"] = list(self.output_lines)
        return d


@dataclass
class SystemMetrics:
    cpu_pct: float = 0.0
    cpu_count: int = 0
    memory_total_gb: float = 0.0
    memory_used_gb: float = 0.0
    memory_available_gb: float = 0.0
    memory_pct: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_pct: float = 0.0
    load_avg: list = field(default_factory=list)
    agents_active: int = 0
    agents_total: int = 0

    def to_dict(self) -> dict:
        return {
            "cpu_pct": self.cpu_pct,
            "cpu_count": self.cpu_count,
            "memory": {
                "total_gb": self.memory_total_gb,
                "used_gb": self.memory_used_gb,
                "available_gb": self.memory_available_gb,
                "pct": self.memory_pct,
            },
            "disk": {
                "total_gb": self.disk_total_gb,
                "used_gb": self.disk_used_gb,
                "pct": self.disk_pct,
            },
            "load_avg": self.load_avg,
            "agents_active": self.agents_active,
            "agents_total": self.agents_total,
        }


# ─────────────────────────────────────────────
# Section 4: AgentManager — THE CORE
# ─────────────────────────────────────────────

class AgentManager:
    def __init__(self, config: Config):
        self.config = config
        self.agents: dict[str, Agent] = {}
        self.tmux_prefix = "ashlar"
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        return self._loop

    # ── tmux helpers (run in executor to avoid blocking) ──

    def _sanitize_for_tmux(self, text: str) -> str:
        """Sanitize text for safe tmux input: strip control chars, truncate."""
        # Strip control characters (\x00-\x1f) except newline (\x0a)
        sanitized = re.sub(r'[\x00-\x09\x0b-\x1f]', '', text)
        # Truncate to 2000 chars
        return sanitized[:2000]

    async def _run_tmux(self, args: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
        return await self.loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["tmux"] + args,
                capture_output=True, text=True, timeout=timeout
            )
        )

    async def _tmux_send_keys(self, session: str, text: str) -> bool:
        try:
            sanitized = self._sanitize_for_tmux(text)
            result = await self._run_tmux(["send-keys", "-t", session, sanitized, "Enter"])
            return result.returncode == 0
        except Exception as e:
            log.error(f"tmux send-keys failed for {session}: {e}")
            return False

    async def _tmux_send_raw(self, session: str, key: str) -> bool:
        """Send a raw key (like C-c) without Enter."""
        try:
            result = await self._run_tmux(["send-keys", "-t", session, key])
            return result.returncode == 0
        except Exception as e:
            log.error(f"tmux send raw key failed for {session}: {e}")
            return False

    async def _tmux_session_exists(self, session: str) -> bool:
        try:
            result = await self._run_tmux(["has-session", "-t", session])
            return result.returncode == 0
        except Exception:
            return False

    async def _tmux_capture(self, session: str, lines: int = 200) -> list[str]:
        try:
            result = await self._run_tmux(["capture-pane", "-t", session, "-p", "-S", f"-{lines}"])
            if result.returncode == 0:
                return result.stdout.splitlines()
            return []
        except Exception:
            return []

    async def _tmux_get_pane_pid(self, session: str) -> int | None:
        try:
            result = await self._run_tmux(["list-panes", "-t", session, "-F", "#{pane_pid}"])
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().split("\n")[0])
        except (ValueError, Exception):
            pass
        return None

    # ── Backend resolution ──

    def _resolve_backend_command(self, backend: str) -> tuple[str, list[str]]:
        """Resolve backend name to (command, args). Falls back to default backend."""
        backend_config = self.config.backends.get(backend)
        if not backend_config:
            log.warning(f"Unknown backend '{backend}', falling back to '{self.config.default_backend}'")
            backend_config = self.config.backends.get(self.config.default_backend, {})
        cmd = backend_config.get("command", "claude")
        args = backend_config.get("args", [])
        if not shutil.which(cmd):
            raise ValueError(f"Backend '{backend}' command not found: {cmd}")
        return cmd, args

    # ── Core operations ──

    async def spawn(
        self,
        role: str = "general",
        name: str | None = None,
        working_dir: str | None = None,
        task: str = "",
        plan_mode: bool = False,
        backend: str = "claude-code",
    ) -> Agent:
        """Spawn a new agent. Returns the Agent object."""
        if len(self.agents) >= self.config.max_agents:
            raise ValueError(f"Maximum agents ({self.config.max_agents}) reached")

        # ── Input validation ──

        # Validate and sanitize name
        if name:
            name = re.sub(r'[\x00-\x1f]', '', name).strip()[:100]
            if not name:
                raise ValueError("Agent name cannot be empty after sanitization")

        # Validate task length
        if task and len(task) > 10000:
            raise ValueError("Task description exceeds 10000 character limit")

        # Validate backend exists in config or is demo mode
        if not self.config.demo_mode:
            if backend not in self.config.backends:
                raise ValueError(f"Unknown backend '{backend}'. Available: {', '.join(self.config.backends.keys())}")

        # Validate working_dir
        if working_dir:
            working_dir = os.path.abspath(os.path.expanduser(working_dir))
            home_dir = str(Path.home())
            config_dirs = [str(ASHLAR_DIR)]
            allowed_prefixes = [home_dir] + config_dirs
            if not any(working_dir.startswith(prefix) for prefix in allowed_prefixes):
                if not os.path.isdir(working_dir):
                    raise ValueError(f"Working directory does not exist and is outside home: {working_dir}")

        # Generate ID
        agent_id = uuid.uuid4().hex[:4]
        while agent_id in self.agents:
            agent_id = uuid.uuid4().hex[:4]

        # Generate name if not provided
        if not name:
            role_name = BUILTIN_ROLES.get(role, BUILTIN_ROLES["general"]).name.split()[0].lower()
            name = f"{role_name}-{agent_id}"

        # Resolve working directory
        if not working_dir:
            working_dir = self.config.default_working_dir
        working_dir = os.path.expanduser(working_dir)
        working_dir = os.path.abspath(working_dir)
        if not os.path.isdir(working_dir):
            os.makedirs(working_dir, exist_ok=True)

        session_name = f"{self.tmux_prefix}-{agent_id}"
        now = datetime.now(timezone.utc).isoformat()

        agent = Agent(
            id=agent_id,
            name=name,
            role=role,
            status="spawning",
            working_dir=working_dir,
            backend=backend if not self.config.demo_mode else "demo",
            task=task,
            summary="Starting up...",
            tmux_session=session_name,
            created_at=now,
            updated_at=now,
            _spawn_time=time.monotonic(),
        )
        agent.last_output_time = time.monotonic()
        self.agents[agent_id] = agent

        # Create tmux session
        try:
            result = await self._run_tmux([
                "new-session", "-d", "-s", session_name,
                "-x", "200", "-y", "50",
                "-c", working_dir,
            ])
            if result.returncode != 0:
                agent.status = "error"
                agent.error_message = f"Failed to create tmux session: {result.stderr}"
                log.error(f"tmux new-session failed: {result.stderr}")
                return agent
        except Exception as e:
            agent.status = "error"
            agent.error_message = str(e)
            return agent

        # Get pane PID
        agent.pid = await self._tmux_get_pane_pid(session_name)

        if self.config.demo_mode:
            # Demo mode: run a bash script that simulates agent behavior
            demo_script = self._build_demo_script(role, task, agent)
            await self._tmux_send_keys(session_name, demo_script)
        else:
            # Real mode: launch backend CLI (claude, codex, or custom)
            try:
                cmd_bin, cmd_args = self._resolve_backend_command(backend)
            except ValueError as e:
                agent.status = "error"
                agent.error_message = str(e)
                log.error(f"Backend resolution failed for {backend}: {e}")
                return agent

            cmd_parts = [cmd_bin] + cmd_args
            cmd = " ".join(cmd_parts)
            await self._tmux_send_keys(session_name, cmd)

            # Wait for CLI to start up
            await asyncio.sleep(3)

            # Send role system prompt as first message
            role_obj = BUILTIN_ROLES.get(role)
            if role_obj and role_obj.system_prompt and task:
                # Combine role context with task
                full_message = f"{role_obj.system_prompt}\n\nYour task: {task}"
                # Send each line separately for long messages
                for line in full_message.split("\n"):
                    if line.strip():
                        await self._tmux_send_keys(session_name, line)
                        await asyncio.sleep(0.1)
            elif task:
                await self._tmux_send_keys(session_name, task)

        agent.status = "working"
        agent.updated_at = datetime.now(timezone.utc).isoformat()
        log.info(f"Spawned agent {agent_id} ({name}) with role {role}")
        return agent

    def _build_demo_script(self, role: str, task: str, agent: Agent | None = None) -> str:
        """Build a multi-phase bash script that simulates realistic agent behavior."""
        import random as _rand
        role_obj = BUILTIN_ROLES.get(role, BUILTIN_ROLES["general"])
        safe_task = task[:80].replace('"', '\\"').replace("'", "")

        # Role-specific working messages with file paths and progress
        role_work = {
            "security": [
                "Reading package.json for dependency audit...",
                "Scanning 142 dependencies for known CVEs...",
                "  ✓ No critical CVEs found in direct dependencies",
                "  ⚠ 3 moderate vulnerabilities in transitive deps",
                "Checking src/auth/login.ts for SQL injection...",
                "Auditing authentication flow in src/middleware/auth.ts...",
                "Found 2 potential XSS vectors in src/handlers/form.ts:47",
                "Reviewing CORS configuration in src/config/cors.ts...",
                "Checking for hardcoded secrets (scanning 89 files)...",
                "  ✓ No secrets detected in source files",
            ],
            "tester": [
                "Analyzing test coverage gaps across 23 modules...",
                "Writing unit tests for src/auth/login.test.ts...",
                "  ✓ Test: should reject invalid credentials (3ms)",
                "  ✓ Test: should rate-limit after 5 attempts (12ms)",
                "Running test suite: 12 passed, 0 failed (1.2s)",
                "Adding integration tests for POST /api/users...",
                "Testing edge cases for payment flow...",
                "  ✓ Test: handles currency conversion (8ms)",
                "  ✗ Test: timeout on webhook retry — needs fix",
                "Coverage report: 78% statements, 65% branches",
            ],
            "frontend": [
                "Reading component tree structure (47 components)...",
                "Analyzing responsive breakpoints in src/styles/...",
                "Editing src/components/Header.tsx (line 34-89)...",
                "  → Refactoring nav items to use flex layout",
                "Writing CSS module: src/components/Dashboard.module.css...",
                "Checking accessibility: adding aria labels to 12 elements...",
                "Editing src/components/Sidebar.tsx...",
                "  → Adding keyboard navigation support",
                "Optimizing bundle: removed 3 unused imports (-12KB)",
                "Running prettier on 8 modified files...",
            ],
            "backend": [
                "Reading database schema (14 tables, 67 columns)...",
                "Analyzing API endpoint patterns in src/routes/...",
                "Creating migration: 003_add_users_table.sql...",
                "  → Adding indexes on email and created_at",
                "Writing validation middleware in src/middleware/validate.ts...",
                "Adding rate limiting to POST /api/auth/* endpoints...",
                "Implementing cursor pagination for GET /api/items...",
                "  → Using created_at + id composite cursor",
                "Writing error handler for 4xx/5xx responses...",
                "Running linter: 0 errors, 2 warnings",
            ],
            "devops": [
                "Reading Dockerfile configuration...",
                "Analyzing CI/CD pipeline (4 stages, 12 jobs)...",
                "Optimizing Docker layer caching in build stage...",
                "  → Separating dependency install from source copy",
                "Configuring health check endpoint at /healthz...",
                "Setting up Prometheus alerting rules...",
                "  → CPU > 80% for 5m → PagerDuty",
                "Writing deployment rollback script: scripts/rollback.sh...",
                "Updating docker-compose.yml with resource limits...",
                "Validating k8s manifests with kubeval...",
            ],
            "architect": [
                "Analyzing system component boundaries (8 services)...",
                "Mapping data flow between auth → api → db...",
                "Evaluating caching strategies: Redis vs in-memory...",
                "  → Recommending Redis for session store (shared state)",
                "Designing event-driven communication pattern...",
                "  → Using NATS for inter-service messaging",
                "Creating sequence diagram for auth flow...",
                "Documenting API contract changes in docs/api-v2.md...",
                "Reviewing scalability: current bottleneck is DB writes...",
                "  → Recommending write-behind cache pattern",
            ],
        }

        work_msgs = role_work.get(role, [
            "Reading project structure (scanning files)...",
            "Analyzing codebase: 34 source files, 12K lines...",
            "Reading src/index.ts for entry point patterns...",
            "Writing implementation in src/features/new-feature.ts...",
            "  → Added 3 functions, 1 class",
            "Running linter checks (eslint)...",
            "  ✓ 0 errors, 0 warnings",
            "Verifying changes compile correctly (tsc --noEmit)...",
            "Checking for regressions in existing tests...",
            "  ✓ All 47 existing tests still pass",
        ])

        def rsleep(lo: float = 0.5, hi: float = 3.0) -> str:
            return f"sleep $(echo \"scale=1; {_rand.uniform(lo, hi):.1f}\" | bc)"

        # Build script lines
        script_lines = [
            '#!/bin/bash',
            f'echo "╭──────────────────────────────────────────╮"',
            f'echo "│ {role_obj.icon} Ashlar Agent (Demo Mode)            │"',
            f'echo "│ Role: {role_obj.name:<34}│"',
            f'echo "╰──────────────────────────────────────────╯"',
            f'echo ""',
            f'echo "Task: {safe_task}"',
            f'echo ""',
            # Phase 1: Planning (10-15s)
            'echo "Planning approach..."',
            rsleep(1.5, 3.0),
            'echo "Let me analyze the codebase structure first."',
            rsleep(1.0, 2.0),
            'echo ""',
            'echo "Here is my plan:"',
            'echo "  1. Read existing code and understand patterns"',
            'echo "  2. Implement the required changes"',
            'echo "  3. Write tests and verify correctness"',
            'echo "  4. Clean up and document"',
            rsleep(2.0, 4.0),
            'echo ""',
        ]

        # Phase 2: Working (30-60s total)
        for i, msg in enumerate(work_msgs):
            script_lines.append(f'echo "{msg}"')
            script_lines.append(rsleep(1.0, 4.0))

        # Phase 3: First question
        script_lines.extend([
            'echo ""',
            'echo "I have completed the initial implementation."',
            'echo "Do you want me to proceed with this approach? (yes/no)"',
            'read -r REPLY',
            'echo ""',
            'echo "Received: $REPLY"',
            'echo "Continuing with additional changes..."',
            rsleep(2.0, 4.0),
        ])

        # Phase 4: Second work phase
        second_phase = [
            "Writing additional test cases...",
            "  ✓ Added 4 edge case tests",
            "Updating documentation...",
            "Running final verification...",
        ]
        for msg in second_phase:
            script_lines.append(f'echo "{msg}"')
            script_lines.append(rsleep(1.5, 3.5))

        # Phase 5: Second question
        script_lines.extend([
            'echo ""',
            'echo "All changes are ready. Should I finalize and commit? (yes/no)"',
            'read -r REPLY',
            'echo ""',
            'echo "Received: $REPLY"',
            rsleep(1.0, 2.0),
            'echo "Finalizing changes..."',
            rsleep(2.0, 3.0),
            'echo "Done! Task completed successfully."',
            'sleep 86400',
        ])

        # Write to temp file and execute
        script_path = Path(tempfile.gettempdir()) / f"ashlar_demo_{uuid.uuid4().hex[:8]}.sh"
        script_path.write_text("\n".join(script_lines))
        script_path.chmod(0o755)
        if agent:
            agent.script_path = str(script_path)
        return f"bash {script_path}"

    async def kill(self, agent_id: str) -> bool:
        """Kill an agent gracefully. Returns the Agent before deletion for archival."""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        session = agent.tmux_session
        log.info(f"Killing agent {agent_id} ({agent.name})")

        # Send /exit to claude
        try:
            await self._tmux_send_keys(session, "/exit")
            await asyncio.sleep(2)
        except Exception:
            pass

        # Force kill tmux session
        try:
            await self._run_tmux(["kill-session", "-t", session])
        except Exception:
            pass

        # Clean up demo script temp file
        if agent.script_path:
            try:
                Path(agent.script_path).unlink(missing_ok=True)
            except Exception:
                pass

        del self.agents[agent_id]
        return True

    async def pause(self, agent_id: str) -> bool:
        """Pause agent by sending Ctrl+C."""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        await self._tmux_send_raw(agent.tmux_session, "C-c")
        agent.status = "paused"
        agent.updated_at = datetime.now(timezone.utc).isoformat()
        log.info(f"Paused agent {agent_id}")
        return True

    async def resume(self, agent_id: str, message: str | None = None) -> bool:
        """Resume a paused agent."""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        msg = message or agent.task or "continue"
        await self._tmux_send_keys(agent.tmux_session, msg)
        agent.status = "working"
        agent.needs_input = False
        agent.input_prompt = None
        agent.updated_at = datetime.now(timezone.utc).isoformat()
        log.info(f"Resumed agent {agent_id}")
        return True

    async def restart(self, agent_id: str) -> bool:
        """Restart an agent by killing its tmux session and re-spawning with same config.
        Updates agent fields in-place on success; sets error on failure without deleting."""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        # Prevent concurrent restarts
        if agent._restart_in_progress:
            log.warning(f"Restart already in progress for agent {agent_id}")
            return False

        agent._restart_in_progress = True
        try:
            log.info(f"Restarting agent {agent_id} ({agent.name}), attempt {agent.restart_count + 1}")

            # Save config references
            saved_role = agent.role
            saved_name = agent.name
            saved_working_dir = agent.working_dir
            saved_backend = agent.backend
            saved_task = agent.task

            # Kill the old tmux session
            old_tmux_session = agent.tmux_session
            try:
                await self._tmux_send_keys(old_tmux_session, "/exit")
                await asyncio.sleep(1)
            except Exception:
                pass
            try:
                await self._run_tmux(["kill-session", "-t", old_tmux_session])
            except Exception:
                pass

            # Verify old session is gone
            try:
                check = await self._run_tmux(["has-session", "-t", old_tmux_session])
                if check.returncode == 0:
                    await self._run_tmux(["kill-session", "-t", old_tmux_session])
            except Exception:
                pass

            # Clean up demo script temp file
            if agent.script_path:
                try:
                    Path(agent.script_path).unlink(missing_ok=True)
                except Exception:
                    pass

            # Create NEW tmux session (same session name)
            session_name = f"{self.tmux_prefix}-{agent_id}"
            now = datetime.now(timezone.utc).isoformat()

            try:
                result = await self._run_tmux([
                    "new-session", "-d", "-s", session_name,
                    "-x", "200", "-y", "50",
                    "-c", saved_working_dir,
                ])
                if result.returncode != 0:
                    # Failed to create new session — set agent to error, don't delete
                    agent.status = "error"
                    agent.error_message = f"Restart failed: could not create tmux session: {result.stderr}"
                    agent._error_entered_at = time.monotonic()
                    agent.updated_at = now
                    return False
            except Exception as e:
                agent.status = "error"
                agent.error_message = f"Restart failed: {e}"
                agent._error_entered_at = time.monotonic()
                agent.updated_at = now
                return False

            # SUCCESS: new tmux session created. Update agent fields in-place.
            agent.tmux_session = session_name
            agent.status = "spawning"
            agent.summary = "Restarting..."
            agent.restart_count += 1
            agent.last_restart_time = time.monotonic()
            agent.restarted_at = now
            agent.updated_at = now
            agent.error_message = None
            agent.needs_input = False
            agent.input_prompt = None
            agent._spawn_time = time.monotonic()
            agent.last_output_time = time.monotonic()
            agent._prev_output_hash = 0
            agent._first_output_received = False
            agent.output_lines.clear()
            agent._total_chars = 0
            agent.context_pct = 0.0
            agent.script_path = None

            # Get pane PID
            agent.pid = await self._tmux_get_pane_pid(session_name)

            if self.config.demo_mode:
                demo_script = self._build_demo_script(saved_role, saved_task, agent)
                await self._tmux_send_keys(session_name, demo_script)
            else:
                try:
                    cmd_bin, cmd_args = self._resolve_backend_command(saved_backend)
                except ValueError as e:
                    agent.status = "error"
                    agent.error_message = str(e)
                    return False

                cmd = " ".join([cmd_bin] + cmd_args)
                await self._tmux_send_keys(session_name, cmd)
                await asyncio.sleep(3)

                role_obj = BUILTIN_ROLES.get(saved_role)
                if role_obj and role_obj.system_prompt and saved_task:
                    full_message = f"{role_obj.system_prompt}\n\nYour task: {saved_task}"
                    for line in full_message.split("\n"):
                        if line.strip():
                            await self._tmux_send_keys(session_name, line)
                            await asyncio.sleep(0.1)
                elif saved_task:
                    await self._tmux_send_keys(session_name, saved_task)

            agent.status = "working"
            agent.updated_at = datetime.now(timezone.utc).isoformat()
            log.info(f"Agent {agent_id} ({saved_name}) restarted successfully (attempt {agent.restart_count})")
            return True
        finally:
            agent._restart_in_progress = False

    async def send_message(self, agent_id: str, message: str) -> bool:
        """Send a message to an agent's tmux session."""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        # Handle multi-line messages
        lines = message.split("\n")
        for line in lines:
            await self._tmux_send_keys(agent.tmux_session, line)
            if len(lines) > 1:
                await asyncio.sleep(0.1)

        agent.needs_input = False
        agent.input_prompt = None
        agent.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    async def capture_output(self, agent_id: str) -> list[str]:
        """Capture terminal output and return new lines since last capture."""
        agent = self.agents.get(agent_id)
        if not agent:
            return []

        raw_lines = await self._tmux_capture(agent.tmux_session)
        if not raw_lines:
            return []

        # Strip trailing empty lines
        while raw_lines and not raw_lines[-1].strip():
            raw_lines.pop()

        # Check if output changed
        output_hash = hash(tuple(raw_lines[-50:])) if raw_lines else 0
        if output_hash == agent._prev_output_hash:
            return []
        agent._prev_output_hash = output_hash

        # Find new lines by comparing with existing buffer
        existing = list(agent.output_lines)
        new_lines = []

        if not existing:
            new_lines = raw_lines
        else:
            # Find where old output ends in new output
            last_existing = existing[-1] if existing else ""
            found_idx = -1
            for i in range(len(raw_lines) - 1, -1, -1):
                if raw_lines[i] == last_existing:
                    found_idx = i
                    break
            if found_idx >= 0 and found_idx < len(raw_lines) - 1:
                new_lines = raw_lines[found_idx + 1:]
            elif found_idx < 0:
                # Couldn't match — treat all as new
                new_lines = raw_lines

        # Update ring buffer (only new lines, not the full capture)
        for line in new_lines:
            agent.output_lines.append(line)

        # Track total chars for context estimation
        agent._total_chars += sum(len(l) for l in new_lines)
        # ~3.5 chars/token for English, 200K context window
        agent.context_pct = min(1.0, (agent._total_chars / 3.5) / 200000)

        return new_lines

    async def detect_status(self, agent_id: str) -> str:
        """Analyze recent output to detect agent's current status."""
        agent = self.agents.get(agent_id)
        if not agent:
            return "error"
        if agent.status == "paused":
            return "paused"

        recent = list(agent.output_lines)[-20:]
        if not recent:
            return agent.status

        return parse_agent_status(recent, agent)

    async def get_agent_memory(self, agent_id: str) -> float:
        """Get RSS memory of agent's process tree in MB."""
        agent = self.agents.get(agent_id)
        if not agent or not agent.pid:
            return 0.0

        try:
            proc = psutil.Process(agent.pid)
            total = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return round(total / 1e6, 1)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    def cleanup_all(self) -> None:
        """Kill all ashlar tmux sessions and clean temp files. Synchronous for shutdown."""
        # Clean up temp demo scripts
        for agent in self.agents.values():
            if agent.script_path:
                try:
                    Path(agent.script_path).unlink(missing_ok=True)
                except Exception:
                    pass

        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for session_name in result.stdout.strip().split("\n"):
                    if session_name.startswith(self.tmux_prefix + "-"):
                        try:
                            subprocess.run(
                                ["tmux", "kill-session", "-t", session_name],
                                capture_output=True, timeout=5
                            )
                        except Exception:
                            pass
        except Exception:
            pass
        log.info("All agent sessions cleaned up")


# ─────────────────────────────────────────────
# Section 5: Status Parser
# ─────────────────────────────────────────────

STATUS_PATTERNS = {
    "planning": [
        re.compile(r"(?i)\bplan\b"),
        re.compile(r"(?i)let me (think|analyze|plan|consider)"),
        re.compile(r"(?i)here'?s (my|the) (plan|approach|strategy)"),
        re.compile(r"(?i)I'll (start by|first|begin)"),
        re.compile(r"(?i)thinking about"),
    ],
    "reading": [
        re.compile(r"(?i)(reading|loading|scanning|parsing) .+\.\w+"),
        re.compile(r"(?i)(reading|loading|scanning|parsing) (directory|folder|project|codebase)"),
        re.compile(r"(?i)exploring .+"),
    ],
    "working": [
        re.compile(r"(?i)(writing|creating|editing|updating) \S+\.\w+"),
        re.compile(r"(?i)(running|executing) .+"),
        re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]"),
        re.compile(r"█+░*"),
        re.compile(r"(?i)Tool Use:"),
        re.compile(r"(?i)Bash:"),
        re.compile(r"(?i)files? (created|edited|read)"),
        re.compile(r"(?i)(checking|auditing|analyzing|testing)"),
        # Git operations
        re.compile(r"(?i)(git (add|commit|push|pull|checkout|merge|rebase))"),
        # Build/compile operations
        re.compile(r"(?i)(building|compiling|bundling|webpack|vite|esbuild)"),
        # Test result patterns (working, not complete — tests are still running)
        re.compile(r"(?i)(\d+ (tests?|specs?) (passed|failed|skipped))"),
    ],
    "waiting": [
        re.compile(r"(?i)(do you want|shall I|should I|would you like)"),
        re.compile(r"(?i)(yes/no|y/n|\[Y/n\]|\[y/N\])"),
        re.compile(r"(?i)proceed\?"),
        re.compile(r"(?i)\bapprove\b"),
    ],
    "error": [
        # Fatal patterns — actual crashes, not just mentions of "error" in output
        re.compile(r"(?i)\b(traceback|fatal|panic|segfault|SIGKILL|SIGSEGV)\b"),
        re.compile(r"(?i)unhandled (exception|error|rejection)"),
        re.compile(r"(?i)command not found"),
        re.compile(r"(?i)permission denied"),
        re.compile(r"(?i)(cannot|couldn'?t) (connect|reach|find|open|read|write)"),
        re.compile(r"(?i)out of memory"),
        re.compile(r"(?i)killed by signal"),
    ],
    # Non-fatal error mentions — tracked for health scoring but don't flip status
    "error_mention": [
        re.compile(r"(?i)(?<!\bno\s)\b(error|exception|failed)\b(?!\s*handl)"),
    ],
    "complete": [
        re.compile(r"(?i)\b(done|complete|finished|successfully)\b"),
        re.compile(r"(?i)task completed"),
        re.compile(r"(?i)all tests pass"),
    ],
}

WAITING_LINE_PATTERNS = [
    re.compile(r"\?\s*$"),
    re.compile(r"(?i)(do you want|shall I|should I|would you like)"),
    re.compile(r"(?i)(yes/no|y/n|\[Y/n\]|\[y/N\])"),
    re.compile(r"(?i)proceed\?"),
]


def parse_agent_status(recent_lines: list[str], agent: Agent) -> str:
    """Parse recent terminal output to detect agent status.
    Priority: waiting > error > reading > planning > working > complete > current status.
    Tracks non-fatal error mentions for health scoring without flipping status."""
    text_block = "\n".join(recent_lines)
    tail_text = "\n".join(recent_lines[-5:])

    # Track non-fatal error mentions for health scoring (don't affect status)
    for pattern in STATUS_PATTERNS.get("error_mention", []):
        if pattern.search(tail_text):
            agent.error_count = min(agent.error_count + 1, 100)

    # Check for waiting (highest priority)
    for pattern in STATUS_PATTERNS["waiting"]:
        if pattern.search(text_block):
            # Extract the question
            agent.needs_input = True
            agent.input_prompt = _extract_question(recent_lines)
            return "waiting"

    # Check last non-empty line for question mark
    last_line = ""
    for line in reversed(recent_lines):
        if line.strip():
            last_line = line.strip()
            break

    for pattern in WAITING_LINE_PATTERNS:
        if pattern.search(last_line):
            agent.needs_input = True
            agent.input_prompt = last_line
            return "waiting"

    # Check for fatal error (only in last 5 lines to avoid old mentions)
    for pattern in STATUS_PATTERNS["error"]:
        if pattern.search(tail_text):
            agent.needs_input = False
            return "error"

    # Check for reading (before working, more specific)
    for pattern in STATUS_PATTERNS["reading"]:
        if pattern.search(tail_text):
            agent.needs_input = False
            return "reading"

    # Check for planning
    for pattern in STATUS_PATTERNS["planning"]:
        if pattern.search(text_block):
            agent.needs_input = False
            return "planning"

    # Check for complete
    for pattern in STATUS_PATTERNS["complete"]:
        if pattern.search(tail_text):
            agent.needs_input = False
            return "idle"

    # Check for working
    for pattern in STATUS_PATTERNS["working"]:
        if pattern.search(text_block):
            agent.needs_input = False
            return "working"

    # No clear signal — keep current status
    agent.needs_input = False
    return agent.status if agent.status != "spawning" else "working"


def _extract_question(lines: list[str]) -> str:
    """Extract the agent's question from recent output lines."""
    # Look backwards for question-like content
    question_lines = []
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            if question_lines:
                break
            continue
        question_lines.insert(0, stripped)
        if len(question_lines) >= 3:
            break
    return "\n".join(question_lines) if question_lines else "Agent needs your input"


_FILE_PATH_RE = re.compile(r"(?:(?:src|lib|test|app|pkg)/)?[\w\-./]+\.\w{1,5}")
_TEST_RESULT_RE = re.compile(r"(?i)(\d+)\s*(?:tests?\s+)?pass(?:ed)?.*?(\d+)\s*fail")
_COVERAGE_RE = re.compile(r"(?i)coverage[:\s]+(\d+)%")
_FILES_PROGRESS_RE = re.compile(r"(?i)(\d+)\s*(?:of|/)\s*(\d+)\s*(?:files?|items?)")

_ACTION_PATTERNS = [
    re.compile(r"(?i)(writing|creating|editing|reading|updating) (.+)"),
    re.compile(r"(?i)(running|executing) (.+)"),
    re.compile(r"(?i)(analyzing|reviewing|checking) (.+)"),
    re.compile(r"(?i)(installing|building|compiling) (.+)"),
    re.compile(r"(?i)(scanning|auditing|testing|deploying) (.+)"),
    re.compile(r"(?i)(found \d+.+)"),
    re.compile(r"(?i)(coverage.+\d+%)"),
]


def extract_summary(lines: list[str], task: str) -> str:
    """Extract a 1-2 line summary from recent output with file paths and test results."""
    # Check for test results
    for line in reversed(lines[-20:]):
        m = _TEST_RESULT_RE.search(line)
        if m:
            return _strip_ansi(f"Tests: {m.group(1)} passed, {m.group(2)} failed")
        m = _COVERAGE_RE.search(line)
        if m:
            return _strip_ansi(f"Coverage: {m.group(1)}%")

    # Extract file paths being worked on
    for line in reversed(lines[-20:]):
        stripped = _strip_ansi(line.strip())
        if not stripped:
            continue

        for pattern in _ACTION_PATTERNS:
            match = pattern.search(stripped)
            if match:
                # Try to extract file path from the match
                fp = _FILE_PATH_RE.search(stripped)
                if fp:
                    return f"{match.group(1).title()} {fp.group(0)}"[:100]
                return stripped[:100]

    # Files progress
    for line in reversed(lines[-15:]):
        m = _FILES_PROGRESS_RE.search(_strip_ansi(line))
        if m:
            return f"Progress: {m.group(1)} of {m.group(2)} files"

    # Fallback: last non-empty line
    for line in reversed(lines[-10:]):
        stripped = _strip_ansi(line.strip())
        if stripped and len(stripped) > 5:
            return stripped[:100]

    return _strip_ansi(task[:100]) if task else "Working..."


# ── LLM-Powered Summary Generation ──

class LLMSummarizer:
    """Async LLM client for generating rich agent summaries via xAI/OpenAI-compatible API."""

    def __init__(self, config: Config):
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._failures: int = 0
        self._max_failures: int = 5  # Circuit breaker threshold
        self._circuit_reset_time: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def summarize(self, output_lines: list[str], task: str, role: str, status: str) -> str | None:
        """Generate a summary from agent output. Returns None on failure (use heuristic fallback)."""
        if not self.config.llm_enabled or not self.config.llm_api_key:
            return None

        # Circuit breaker: if too many failures, back off
        if self._failures >= self._max_failures:
            if time.monotonic() < self._circuit_reset_time:
                return None
            # Reset circuit after cooldown
            self._failures = 0

        # Truncate output to configured max lines
        recent = output_lines[-self.config.llm_max_output_lines:]
        if not recent:
            return None

        output_text = _strip_ansi("\n".join(recent))

        prompt = (
            f"You are summarizing an AI coding agent's terminal output.\n"
            f"Agent role: {role}\nAgent status: {status}\nTask: {task}\n\n"
            f"Recent terminal output:\n```\n{output_text}\n```\n\n"
            f"Write a concise 1-sentence summary (max 100 chars) of what the agent is currently doing. "
            f"Focus on the specific action and file/component being worked on. "
            f"Examples: 'Writing auth middleware in src/auth.ts', 'Running test suite — 12/15 passing', "
            f"'Found 2 XSS vulnerabilities in form handler'. Do NOT include quotes."
        )

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.config.llm_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.3,
                },
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if content:
                        self._failures = 0
                        return content[:100]
                elif resp.status == 429:
                    log.warning("LLM rate limited, backing off")
                    self._failures += 2
                else:
                    log.debug(f"LLM API returned {resp.status}")
                    self._failures += 1
        except asyncio.TimeoutError:
            log.debug("LLM summary request timed out")
            self._failures += 1
        except Exception as e:
            log.debug(f"LLM summary error: {e}")
            self._failures += 1

        # Set circuit breaker cooldown
        if self._failures >= self._max_failures:
            self._circuit_reset_time = time.monotonic() + 60.0
            log.warning("LLM circuit breaker tripped, cooling down for 60s")

        return None


# ── Phase detection for progress estimation ──

PHASE_PATTERNS = {
    "planning": [
        re.compile(r"(?i)(plan|approach|strategy|think|analyze|consider)"),
    ],
    "reading": [
        re.compile(r"(?i)(reading|scanning|exploring|listing) "),
    ],
    "implementing": [
        re.compile(r"(?i)(writing|creating|editing|modifying|adding|removing)"),
        re.compile(r"(?i)(implementing|building|refactoring)"),
    ],
    "testing": [
        re.compile(r"(?i)(running tests|test suite|coverage|verif)"),
        re.compile(r"(?i)(checking|linting|validating)"),
    ],
    "complete": [
        re.compile(r"(?i)\b(done|complete|finished|successfully)\b"),
    ],
}

PHASE_PROGRESS = {
    "unknown": 0.0,
    "planning": 0.10,
    "reading": 0.25,
    "implementing": 0.50,
    "testing": 0.80,
    "complete": 1.0,
}


def detect_phase(lines: list[str]) -> str:
    """Detect the current work phase from recent output."""
    recent = lines[-15:]
    text = "\n".join(recent)

    # Check from most advanced phase backwards
    for phase in ("complete", "testing", "implementing", "reading", "planning"):
        for pat in PHASE_PATTERNS[phase]:
            if pat.search(text):
                return phase
    return "unknown"


def estimate_progress(agent: Agent) -> float:
    """Estimate agent progress as 0.0–1.0."""
    phase = detect_phase(list(agent.output_lines))
    agent._phase = phase
    agent.phase = phase
    base = PHASE_PROGRESS.get(phase, 0.0)

    # Add time-based interpolation within the phase
    if agent.created_at:
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(agent.created_at)).total_seconds()
            # Agents typically run 60–300s; add small time bonus
            time_bonus = min(0.10, elapsed / 600)
            return min(1.0, base + time_bonus)
        except Exception:
            pass
    return base


def calculate_health_score(agent: Agent, memory_limit_mb: int = 2048) -> float:
    """Calculate composite health score (0.0–1.0) from uptime, errors, output rate, memory.

    Components:
      - uptime_factor: ramps up to 1.0 over 10 minutes (longer uptime = healthier)
      - error_factor: fewer errors = better (1.0 at 0 errors, decays toward 0.2)
      - output_factor: some output = good, no output for >60s = concerning
      - memory_factor: under limit = 1.0, degrades linearly above 75% of limit
    """
    now = time.monotonic()

    # Uptime factor: ramp from 0.5 to 1.0 over 600s (10 min)
    if agent._spawn_time > 0:
        uptime_s = now - agent._spawn_time
        uptime_factor = min(1.0, 0.5 + (uptime_s / 1200))  # 0.5 base, full at 10min
    else:
        uptime_factor = 0.5

    # Error factor: exponential decay based on error count
    # 0 errors → 1.0, 5 errors → ~0.6, 10+ errors → ~0.35
    error_factor = max(0.2, 1.0 / (1.0 + agent.error_count * 0.15))

    # Output factor: penalize stale agents (no output for >60s)
    if agent.last_output_time > 0:
        silence_s = now - agent.last_output_time
        if silence_s < 30:
            output_factor = 1.0
        elif silence_s < 120:
            output_factor = max(0.3, 1.0 - (silence_s - 30) / 180)
        else:
            output_factor = 0.3
    elif agent._spawn_time > 0 and (now - agent._spawn_time) > 30:
        # Never received output but agent has been alive >30s
        output_factor = 0.4
    else:
        output_factor = 0.8  # just spawned, no output yet is fine

    # Memory factor: 1.0 under 75% of limit, linear decay above
    if memory_limit_mb > 0 and agent.memory_mb > 0:
        mem_ratio = agent.memory_mb / memory_limit_mb
        if mem_ratio < 0.75:
            memory_factor = 1.0
        else:
            memory_factor = max(0.1, 1.0 - (mem_ratio - 0.75) * 4.0)
    else:
        memory_factor = 1.0

    # Error/paused status override
    if agent.status == "error":
        return max(0.05, error_factor * 0.3)
    if agent.status == "paused":
        return 0.5  # neutral — paused by user, not unhealthy

    # Weighted composite
    score = (
        uptime_factor * 0.20 +
        error_factor * 0.35 +
        output_factor * 0.25 +
        memory_factor * 0.20
    )
    return round(min(1.0, max(0.0, score)), 3)


# ─────────────────────────────────────────────
# Section 5b: Database (SQLite Persistence)
# ─────────────────────────────────────────────

class Database:
    """Async SQLite layer for agent history, projects, and workflows."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (ASHLAR_DIR / "ashlar.db")
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS agents_history (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                project_id TEXT,
                task TEXT,
                summary TEXT,
                status TEXT,
                working_dir TEXT,
                backend TEXT,
                created_at TEXT,
                completed_at TEXT,
                duration_sec INTEGER,
                context_pct REAL,
                output_preview TEXT
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                agents_json TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_messages (
                id TEXT PRIMARY KEY,
                from_agent_id TEXT NOT NULL,
                to_agent_id TEXT,
                content TEXT NOT NULL,
                created_at TEXT,
                read_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_agent_messages_to ON agent_messages(to_agent_id);
            CREATE INDEX IF NOT EXISTS idx_history_completed ON agents_history(completed_at);
            CREATE INDEX IF NOT EXISTS idx_messages_from ON agent_messages(from_agent_id);
            CREATE INDEX IF NOT EXISTS idx_messages_created ON agent_messages(created_at);
        """)
        await self._db.commit()

        # Seed built-in workflows if empty
        async with self._db.execute("SELECT COUNT(*) FROM workflows") as cur:
            row = await cur.fetchone()
            if row[0] == 0:
                await self._seed_default_workflows()

        log.info(f"Database initialized at {self.db_path}")

    async def _seed_default_workflows(self) -> None:
        defaults = [
            {
                "id": "builtin-code-review",
                "name": "Code Review",
                "description": "Backend + Security + Reviewer agents for thorough code review",
                "agents_json": json.dumps([
                    {"role": "backend", "task": "Review the codebase for bugs and logic errors"},
                    {"role": "security", "task": "Audit for security vulnerabilities"},
                    {"role": "reviewer", "task": "Review code quality and suggest improvements"},
                ]),
            },
            {
                "id": "builtin-full-stack",
                "name": "Full Stack",
                "description": "Frontend + Backend + Tester for full-stack development",
                "agents_json": json.dumps([
                    {"role": "frontend", "task": "Build the frontend components"},
                    {"role": "backend", "task": "Build the API and backend logic"},
                    {"role": "tester", "task": "Write comprehensive tests"},
                ]),
            },
        ]
        for wf in defaults:
            await self._db.execute(
                "INSERT INTO workflows (id, name, description, agents_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (wf["id"], wf["name"], wf["description"], wf["agents_json"],
                 datetime.now(timezone.utc).isoformat()),
            )
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Agent History ──

    async def save_agent(self, agent: Agent) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        created = agent.created_at or completed_at
        try:
            created_dt = datetime.fromisoformat(created)
            completed_dt = datetime.fromisoformat(completed_at)
            duration = int((completed_dt - created_dt).total_seconds())
        except Exception:
            duration = 0

        output_preview = "\n".join(list(agent.output_lines)[-50:])

        await self._db.execute(
            """INSERT OR REPLACE INTO agents_history
               (id, name, role, project_id, task, summary, status, working_dir,
                backend, created_at, completed_at, duration_sec, context_pct, output_preview)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent.id, agent.name, agent.role, agent.project_id, agent.task,
             agent.summary, agent.status, agent.working_dir, agent.backend,
             agent.created_at, completed_at, duration, agent.context_pct, output_preview),
        )
        await self._db.commit()

    async def get_agent_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM agents_history ORDER BY completed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_agent_history_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM agents_history") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_agent_history_item(self, agent_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM agents_history WHERE id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Projects ──

    async def save_project(self, project: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT OR REPLACE INTO projects (id, name, path, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project["id"], project["name"], project["path"],
             project.get("description", ""), project.get("created_at", now), now),
        )
        await self._db.commit()

    async def get_projects(self) -> list[dict]:
        async with self._db.execute("SELECT * FROM projects ORDER BY name") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def delete_project(self, project_id: str) -> bool:
        async with self._db.execute("DELETE FROM projects WHERE id = ?", (project_id,)) as cur:
            await self._db.commit()
            return cur.rowcount > 0

    # ── Workflows ──

    async def save_workflow(self, workflow: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        agents_json = workflow.get("agents_json", "")
        if isinstance(agents_json, list):
            agents_json = json.dumps(agents_json)
        await self._db.execute(
            """INSERT OR REPLACE INTO workflows (id, name, description, agents_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (workflow["id"], workflow["name"], workflow.get("description", ""),
             agents_json, workflow.get("created_at", now)),
        )
        await self._db.commit()

    async def get_workflows(self) -> list[dict]:
        async with self._db.execute("SELECT * FROM workflows ORDER BY name") as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["agents"] = json.loads(d.pop("agents_json", "[]"))
                except Exception:
                    d["agents"] = []
                result.append(d)
            return result

    async def get_workflow(self, workflow_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["agents"] = json.loads(d.pop("agents_json", "[]"))
            except Exception:
                d["agents"] = []
            return d

    async def delete_workflow(self, workflow_id: str) -> bool:
        async with self._db.execute(
            "DELETE FROM workflows WHERE id = ?", (workflow_id,)
        ) as cur:
            await self._db.commit()
            return cur.rowcount > 0

    # ── Agent Messages ──

    async def save_message(self, msg: dict) -> None:
        await self._db.execute(
            """INSERT INTO agent_messages (id, from_agent_id, to_agent_id, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (msg["id"], msg["from_agent_id"], msg.get("to_agent_id"),
             msg["content"], msg["created_at"]),
        )
        await self._db.commit()

    async def get_messages_for_agent(self, agent_id: str, limit: int = 50) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM agent_messages WHERE to_agent_id = ? ORDER BY created_at DESC LIMIT ?",
            (agent_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_messages_between(self, agent_a: str, agent_b: str, limit: int = 50) -> list[dict]:
        async with self._db.execute(
            """SELECT * FROM agent_messages
               WHERE (from_agent_id = ? AND to_agent_id = ?)
                  OR (from_agent_id = ? AND to_agent_id = ?)
               ORDER BY created_at DESC LIMIT ?""",
            (agent_a, agent_b, agent_b, agent_a, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_message_count_for_agent(self, agent_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE to_agent_id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def mark_messages_read(self, agent_id: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.execute(
            "UPDATE agent_messages SET read_at = ? WHERE to_agent_id = ? AND read_at IS NULL",
            (now, agent_id),
        ) as cur:
            await self._db.commit()
            return cur.rowcount

    async def get_unread_count(self, agent_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE to_agent_id = ? AND read_at IS NULL",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ─────────────────────────────────────────────
# Section 6: Metrics Collector
# ─────────────────────────────────────────────

# Initialize CPU percent baseline
psutil.cpu_percent()


async def collect_system_metrics(agent_manager: AgentManager) -> SystemMetrics:
    """Collect system-wide metrics."""
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    active = sum(1 for a in agent_manager.agents.values() if a.status in ("working", "planning"))

    return SystemMetrics(
        cpu_pct=round(cpu, 1),
        cpu_count=psutil.cpu_count() or 1,
        memory_total_gb=round(mem.total / 1e9, 1),
        memory_used_gb=round(mem.used / 1e9, 1),
        memory_available_gb=round(mem.available / 1e9, 1),
        memory_pct=round(mem.percent, 1),
        disk_total_gb=round(disk.total / 1e9, 1),
        disk_used_gb=round(disk.used / 1e9, 1),
        disk_pct=round(disk.percent, 1),
        load_avg=[round(x, 2) for x in os.getloadavg()],
        agents_active=active,
        agents_total=len(agent_manager.agents),
    )


# ─────────────────────────────────────────────
# Section 7: WebSocket Hub
# ─────────────────────────────────────────────

class WebSocketHub:
    def __init__(self, agent_manager: AgentManager, config: Config, db: Database | None = None):
        self.clients: set[web.WebSocketResponse] = set()
        self.agent_manager = agent_manager
        self.config = config
        self.db = db

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self.clients.add(ws)
        log.info(f"WebSocket client connected ({len(self.clients)} total)")

        try:
            # Send full state sync
            projects = await self.db.get_projects() if self.db else []
            workflows = await self.db.get_workflows() if self.db else []
            backends_info = {}
            for name, cfg in self.config.backends.items():
                backends_info[name] = {
                    "name": name,
                    "command": cfg.get("command", ""),
                    "available": bool(shutil.which(cfg.get("command", ""))),
                }
            await ws.send_json({
                "type": "sync",
                "agents": [a.to_dict() for a in self.agent_manager.agents.values()],
                "projects": projects,
                "workflows": workflows,
                "config": self.config.to_dict(),
                "backends": backends_info,
            })

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self.handle_message(data, ws)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "Invalid JSON"})
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    log.error(f"WebSocket error: {ws.exception()}")
                    break
        except Exception as e:
            log.error(f"WebSocket handler error: {e}")
        finally:
            self.clients.discard(ws)
            log.info(f"WebSocket client disconnected ({len(self.clients)} total)")

        return ws

    async def handle_message(self, data: dict, ws: web.WebSocketResponse) -> None:
        msg_type = data.get("type")

        match msg_type:
            case "spawn":
                try:
                    agent = await self.agent_manager.spawn(
                        role=data.get("role", self.config.default_role),
                        name=data.get("name"),
                        working_dir=data.get("working_dir"),
                        task=data.get("task", ""),
                        plan_mode=data.get("plan_mode", False),
                        backend=data.get("backend", "claude-code"),
                    )
                    if data.get("project_id"):
                        agent.project_id = data["project_id"]
                    await self.broadcast({
                        "type": "agent_update",
                        "agent": agent.to_dict(),
                    })
                    await self.broadcast({
                        "type": "event",
                        "event": "agent_spawned",
                        "agent_id": agent.id,
                        "message": f"Agent {agent.name} spawned",
                    })
                except ValueError as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            case "send":
                agent_id = data.get("agent_id")
                message = data.get("message", "")
                if agent_id and message:
                    await self.agent_manager.send_message(agent_id, message)
                    agent = self.agent_manager.agents.get(agent_id)
                    if agent:
                        await self.broadcast({"type": "agent_update", "agent": agent.to_dict()})

            case "kill":
                agent_id = data.get("agent_id")
                if agent_id:
                    agent = self.agent_manager.agents.get(agent_id)
                    name = agent.name if agent else "unknown"
                    # Archive to history before killing
                    if agent and self.db:
                        try:
                            await self.db.save_agent(agent)
                        except Exception as e:
                            log.warning(f"Failed to archive agent {agent_id}: {e}")
                    success = await self.agent_manager.kill(agent_id)
                    if success:
                        await self.broadcast({
                            "type": "agent_removed",
                            "agent_id": agent_id,
                        })
                        await self.broadcast({
                            "type": "event",
                            "event": "agent_killed",
                            "agent_id": agent_id,
                            "message": f"Agent {name} killed",
                        })

            case "pause":
                agent_id = data.get("agent_id")
                if agent_id:
                    await self.agent_manager.pause(agent_id)
                    agent = self.agent_manager.agents.get(agent_id)
                    if agent:
                        await self.broadcast({"type": "agent_update", "agent": agent.to_dict()})

            case "resume":
                agent_id = data.get("agent_id")
                message = data.get("message")
                if agent_id:
                    await self.agent_manager.resume(agent_id, message)
                    agent = self.agent_manager.agents.get(agent_id)
                    if agent:
                        await self.broadcast({"type": "agent_update", "agent": agent.to_dict()})

            case "agent_message":
                from_id = data.get("from_agent_id")
                to_id = data.get("to_agent_id")
                content = data.get("content", "")
                if from_id and to_id and content and self.db:
                    to_agent = self.agent_manager.agents.get(to_id)
                    if not to_agent:
                        await ws.send_json({"type": "error", "message": f"Target agent {to_id} not found"})
                    else:
                        msg = {
                            "id": uuid.uuid4().hex[:8],
                            "from_agent_id": from_id,
                            "to_agent_id": to_id,
                            "content": content,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                        await self.db.save_message(msg)
                        to_agent.unread_messages += 1
                        # Also send to agent's tmux session
                        from_agent = self.agent_manager.agents.get(from_id)
                        from_name = from_agent.name if from_agent else from_id
                        sanitized = content.strip()[:500]
                        sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', sanitized)  # Strip control chars except newline/tab
                        await self.agent_manager.send_message(to_id, f"[Message from {from_name}]: {sanitized}")
                        await self.broadcast({"type": "agent_message", "message": msg})
                        await self.broadcast({"type": "agent_update", "agent": to_agent.to_dict()})

            case "sync_request":
                projects = await self.db.get_projects() if self.db else []
                workflows = await self.db.get_workflows() if self.db else []
                backends_info = {}
                for name, cfg in self.config.backends.items():
                    backends_info[name] = {
                        "name": name,
                        "command": cfg.get("command", ""),
                        "available": bool(shutil.which(cfg.get("command", ""))),
                    }
                await ws.send_json({
                    "type": "sync",
                    "agents": [a.to_dict() for a in self.agent_manager.agents.values()],
                    "projects": projects,
                    "workflows": workflows,
                    "config": self.config.to_dict(),
                    "backends": backends_info,
                })

            case _:
                await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    async def broadcast(self, message: dict) -> None:
        if not self.clients:
            return
        dead: set[web.WebSocketResponse] = set()
        for ws in self.clients:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
            except (ConnectionError, RuntimeError, ConnectionResetError, asyncio.TimeoutError):
                dead.add(ws)
        self.clients -= dead


# ─────────────────────────────────────────────
# Section 8: REST API Handlers
# ─────────────────────────────────────────────

async def serve_dashboard(request: web.Request) -> web.FileResponse:
    dashboard_path = Path(__file__).parent / "ashlar_dashboard.html"
    if not dashboard_path.exists():
        return web.Response(text="Dashboard not found. Create ashlar_dashboard.html.", status=404)
    return web.FileResponse(dashboard_path)


async def serve_logo(request: web.Request) -> web.Response:
    logo_path = Path(__file__).parent / "White Ashlar logo copy.png"
    if not logo_path.exists():
        return web.Response(text="Logo not found", status=404)
    return web.FileResponse(logo_path, headers={"Cache-Control": "public, max-age=86400"})


async def list_agents(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    agents = [a.to_dict() for a in manager.agents.values()]
    return web.json_response(agents)


async def spawn_agent(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # REST-level input validation (before calling manager.spawn)
    name = data.get("name")
    if name is not None:
        if not isinstance(name, str):
            return web.json_response({"error": "name must be a string"}, status=400)
        name = re.sub(r'[\x00-\x1f]', '', name).strip()[:100]
        if not name:
            return web.json_response({"error": "name cannot be empty"}, status=400)

    task = data.get("task", "")
    if not isinstance(task, str):
        return web.json_response({"error": "task must be a string"}, status=400)
    if len(task) > 10000:
        return web.json_response({"error": "task exceeds 10000 character limit"}, status=400)

    role = data.get("role", request.app["config"].default_role)
    if role not in BUILTIN_ROLES:
        return web.json_response({"error": f"Unknown role '{role}'. Available: {', '.join(BUILTIN_ROLES.keys())}"}, status=400)

    backend = data.get("backend", "claude-code")
    if not isinstance(backend, str):
        return web.json_response({"error": "backend must be a string"}, status=400)

    working_dir = data.get("working_dir")
    if working_dir is not None and not isinstance(working_dir, str):
        return web.json_response({"error": "working_dir must be a string"}, status=400)

    try:
        agent = await manager.spawn(
            role=role,
            name=name,
            working_dir=working_dir,
            task=task,
            plan_mode=data.get("plan_mode", False),
            backend=backend,
        )

        # Broadcast to WebSocket clients
        hub: WebSocketHub = request.app["ws_hub"]
        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
        await hub.broadcast({
            "type": "event",
            "event": "agent_spawned",
            "agent_id": agent.id,
            "message": f"Agent {agent.name} spawned",
        })

        return web.json_response(agent.to_dict(), status=201)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=503)


async def get_agent(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    agent_id = request.match_info["id"]
    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)
    return web.json_response(agent.to_dict_full())


async def delete_agent(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    db: Database = request.app["db"]
    agent_id = request.match_info["id"]

    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    # Archive to history before killing
    try:
        await db.save_agent(agent)
    except Exception as e:
        log.warning(f"Failed to archive agent {agent_id}: {e}")

    name = agent.name
    success = await manager.kill(agent_id)
    if success:
        await hub.broadcast({
            "type": "agent_removed",
            "agent_id": agent_id,
        })
        await hub.broadcast({
            "type": "event",
            "event": "agent_killed",
            "agent_id": agent_id,
            "message": f"Agent {name} killed",
        })
        return web.json_response({"status": "killed"})
    return web.json_response({"error": "Failed to kill agent"}, status=500)


async def send_to_agent(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    agent_id = request.match_info["id"]

    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message = data.get("message", "")
    if not message:
        return web.json_response({"error": "No message provided"}, status=400)

    success = await manager.send_message(agent_id, message)
    if success:
        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
        return web.json_response({"status": "sent"})
    return web.json_response({"error": "Failed to send message"}, status=500)


async def pause_agent(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    agent_id = request.match_info["id"]

    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    success = await manager.pause(agent_id)
    if success:
        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
        return web.json_response({"status": "paused"})
    return web.json_response({"error": "Failed to pause"}, status=500)


async def resume_agent(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    agent_id = request.match_info["id"]

    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        data = await request.json()
        message = data.get("message")
    except Exception:
        message = None

    success = await manager.resume(agent_id, message)
    if success:
        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
        return web.json_response({"status": "resumed"})
    return web.json_response({"error": "Failed to resume"}, status=500)


async def restart_agent(request: web.Request) -> web.Response:
    """POST /api/agents/{id}/restart — Manually restart an agent."""
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    agent_id = request.match_info["id"]

    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        success = await manager.restart(agent_id)
        if success:
            restarted = manager.agents.get(agent_id)
            if restarted:
                await hub.broadcast({"type": "agent_update", "agent": restarted.to_dict()})
                await hub.broadcast({
                    "type": "event",
                    "event": "agent_restarted",
                    "agent_id": agent_id,
                    "message": f"Agent {restarted.name} manually restarted (attempt {restarted.restart_count})",
                })
                return web.json_response({"status": "restarted", "restart_count": restarted.restart_count})
        return web.json_response({"error": "Restart failed"}, status=500)
    except Exception as e:
        log.error(f"Restart endpoint error for {agent_id}: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def system_metrics(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    metrics = await collect_system_metrics(manager)
    return web.json_response(metrics.to_dict())


async def list_roles(request: web.Request) -> web.Response:
    roles = {k: v.to_dict() for k, v in BUILTIN_ROLES.items()}
    return web.json_response(roles)


async def get_agent_output(request: web.Request) -> web.Response:
    manager: AgentManager = request.app["agent_manager"]
    agent_id = request.match_info["id"]
    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", 200))
    except ValueError:
        return web.json_response({"error": "offset and limit must be integers"}, status=400)

    # Clamp limit to max 1000
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    all_lines = list(agent.output_lines)
    total = len(all_lines)
    sliced = all_lines[offset:offset + limit]

    return web.json_response({
        "data": sliced,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


async def get_config(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    return web.json_response(config.to_dict())


async def put_config(request: web.Request) -> web.Response:
    """Update runtime config and save to ashlar.yaml."""
    config: Config = request.app["config"]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Validation rules
    validators = {
        "max_agents": lambda v: isinstance(v, int) and 1 <= v <= 100,
        "default_role": lambda v: isinstance(v, str) and v in BUILTIN_ROLES,
        "default_working_dir": lambda v: isinstance(v, str) and len(v) > 0,
        "output_capture_interval": lambda v: isinstance(v, (int, float)) and 0.5 <= v <= 30.0,
        "memory_limit_mb": lambda v: isinstance(v, int) and 256 <= v <= 32768,
        "default_backend": lambda v: isinstance(v, str) and v in config.backends,
        "llm_enabled": lambda v: isinstance(v, bool),
        "llm_model": lambda v: isinstance(v, str) and len(v) > 0,
        "llm_summary_interval": lambda v: isinstance(v, (int, float)) and 3.0 <= v <= 120.0,
        "max_restarts": lambda v: isinstance(v, int),
    }

    # Clamp max_restarts to [1, 10] range
    if "max_restarts" in data and isinstance(data["max_restarts"], int):
        data["max_restarts"] = max(1, min(10, data["max_restarts"]))

    errors = []
    for key, value in data.items():
        if key in validators and not validators[key](value):
            errors.append(f"Invalid value for {key}: {value}")

    if errors:
        return web.json_response({"error": "; ".join(errors)}, status=400)

    allowed_keys = set(validators.keys())

    # Build YAML-safe update dict
    yaml_update = {}
    agents_keys = {"max_agents": "max_concurrent", "default_role": "default_role",
                   "default_working_dir": "default_working_dir",
                   "output_capture_interval": "output_capture_interval_sec",
                   "memory_limit_mb": "memory_limit_mb", "default_backend": "default_backend"}
    llm_keys = {"llm_enabled": "enabled", "llm_model": "model", "llm_summary_interval": "summary_interval_sec"}

    for key, value in data.items():
        if key not in allowed_keys:
            continue
        if key in agents_keys:
            yaml_update.setdefault("agents", {})[agents_keys[key]] = value
        elif key in llm_keys:
            yaml_update.setdefault("llm", {})[llm_keys[key]] = value

    # FIRST: write YAML to disk. Only update in-memory config on success.
    config_path = ASHLAR_DIR / "ashlar.yaml"
    try:
        raw = DEFAULT_CONFIG.copy()
        if config_path.exists():
            with open(config_path) as f:
                raw = deep_merge(raw, yaml.safe_load(f) or {})
        raw = deep_merge(raw, yaml_update)
        # Atomic write: write to temp then rename
        tmp_path = config_path.with_suffix(".yaml.tmp")
        with open(tmp_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
        tmp_path.rename(config_path)
        log.info(f"Config saved to disk: {', '.join(data.keys())}")
    except Exception as e:
        log.warning(f"Failed to save config to disk: {e}")
        # Do NOT update in-memory config — disk write failed
        return web.json_response({"error": f"Failed to save: {e}", "config": config.to_dict()}, status=500)

    # THEN: update in-memory config (disk write succeeded)
    for key in allowed_keys:
        if key in data and hasattr(config, key):
            setattr(config, key, data[key])

    # If LLM was enabled/disabled, update summarizer
    summarizer = request.app.get("llm_summarizer")
    if summarizer and "llm_enabled" in data:
        summarizer.config = config

    return web.json_response(config.to_dict())


# ── Project endpoints ──

async def list_projects(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    projects = await db.get_projects()
    return web.json_response(projects)


async def create_project(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not data.get("name") or not data.get("path"):
        return web.json_response({"error": "name and path are required"}, status=400)

    project = {
        "id": uuid.uuid4().hex[:8],
        "name": data["name"],
        "path": os.path.expanduser(data["path"]),
        "description": data.get("description", ""),
    }
    await db.save_project(project)
    return web.json_response(project, status=201)


async def delete_project(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    project_id = request.match_info["id"]
    success = await db.delete_project(project_id)
    if success:
        return web.json_response({"status": "deleted"})
    return web.json_response({"error": "Project not found"}, status=404)


# ── Workflow endpoints ──

async def list_workflows(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    workflows = await db.get_workflows()
    return web.json_response(workflows)


async def create_workflow(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not data.get("name") or not data.get("agents"):
        return web.json_response({"error": "name and agents are required"}, status=400)

    workflow = {
        "id": uuid.uuid4().hex[:8],
        "name": data["name"],
        "description": data.get("description", ""),
        "agents_json": data["agents"],
    }
    await db.save_workflow(workflow)
    workflow["agents"] = data["agents"]
    return web.json_response(workflow, status=201)


async def run_workflow(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    workflow_id = request.match_info["id"]

    workflow = await db.get_workflow(workflow_id)
    if not workflow:
        return web.json_response({"error": "Workflow not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    working_dir = body.get("working_dir")

    # Capacity pre-check
    config: Config = request.app["config"]
    agents_needed = len(workflow.get("agents", []))
    current_count = len(manager.agents)
    if current_count + agents_needed > config.max_agents:
        return web.json_response({
            "error": f"Not enough capacity: need {agents_needed} agents, "
                     f"but only {config.max_agents - current_count} slots available "
                     f"({current_count}/{config.max_agents} in use)",
        }, status=503)

    agent_ids = []
    failed = []
    related = []
    for agent_def in workflow.get("agents", []):
        try:
            agent = await manager.spawn(
                role=agent_def.get("role", "general"),
                name=agent_def.get("name"),
                working_dir=agent_def.get("working_dir") or working_dir,
                task=agent_def.get("task", ""),
            )
            agent_ids.append(agent.id)
            related.append(agent.id)
        except ValueError as e:
            log.warning(f"Workflow spawn failed: {e}")
            failed.append({"role": agent_def.get("role", "general"), "error": str(e)})

    # Link related agents
    for aid in agent_ids:
        a = manager.agents.get(aid)
        if a:
            a.related_agents = [x for x in related if x != aid]
            await hub.broadcast({"type": "agent_update", "agent": a.to_dict()})

    await hub.broadcast({
        "type": "event",
        "event": "workflow_started",
        "message": f"Workflow '{workflow['name']}' started ({len(agent_ids)} agents)",
    })

    result: dict[str, Any] = {"agent_ids": agent_ids, "workflow": workflow["name"]}
    if failed:
        result["spawned"] = agent_ids
        result["failed"] = failed

    return web.json_response(result)


async def delete_workflow(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    workflow_id = request.match_info["id"]
    if workflow_id.startswith("builtin-"):
        return web.json_response({"error": "Cannot delete built-in workflows"}, status=400)
    success = await db.delete_workflow(workflow_id)
    if success:
        return web.json_response({"status": "deleted"})
    return web.json_response({"error": "Workflow not found"}, status=404)


async def update_workflow(request: web.Request) -> web.Response:
    """PUT /api/workflows/{id} — update an existing workflow."""
    db: Database = request.app["db"]
    workflow_id = request.match_info["id"]

    if workflow_id.startswith("builtin-"):
        return web.json_response({"error": "Cannot edit built-in workflows"}, status=400)

    existing = await db.get_workflow(workflow_id)
    if not existing:
        return web.json_response({"error": "Workflow not found"}, status=404)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = data.get("name", existing["name"])
    description = data.get("description", existing.get("description", ""))
    agents = data.get("agents", existing.get("agents", []))

    if not name or not agents:
        return web.json_response({"error": "name and agents are required"}, status=400)

    # Validate agent roles
    for agent_spec in agents:
        if agent_spec.get("role") and agent_spec["role"] not in BUILTIN_ROLES:
            return web.json_response({"error": f"Unknown role: {agent_spec['role']}"}, status=400)

    workflow = {
        "id": workflow_id,
        "name": name,
        "description": description,
        "agents_json": agents,
        "created_at": existing.get("created_at", datetime.now(timezone.utc).isoformat()),
    }
    await db.save_workflow(workflow)

    return web.json_response({"id": workflow_id, "name": name, "description": description, "agents": agents})


# ── Backend endpoints ──

async def list_backends(request: web.Request) -> web.Response:
    """GET /api/backends — list available backends with availability."""
    config: Config = request.app["config"]
    result = {}
    for name, cfg in config.backends.items():
        result[name] = {
            "name": name,
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []),
            "available": bool(shutil.which(cfg.get("command", ""))),
        }
    return web.json_response(result)


# ── Agent Messaging endpoints ──

async def send_agent_message(request: web.Request) -> web.Response:
    """POST /api/agents/{id}/message — send message from one agent to another."""
    db: Database = request.app["db"]
    hub: WebSocketHub = request.app["ws_hub"]
    manager: AgentManager = request.app["agent_manager"]
    from_agent_id = request.match_info["id"]

    from_agent = manager.agents.get(from_agent_id)
    if not from_agent:
        return web.json_response({"error": "Sender agent not found"}, status=404)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    to_agent_id = data.get("to_agent_id")
    content = data.get("content", "")
    if not to_agent_id or not content:
        return web.json_response({"error": "to_agent_id and content required"}, status=400)

    to_agent = manager.agents.get(to_agent_id)
    if not to_agent:
        return web.json_response({"error": "Target agent not found"}, status=404)

    msg = {
        "id": uuid.uuid4().hex[:8],
        "from_agent_id": from_agent_id,
        "to_agent_id": to_agent_id,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.save_message(msg)
    to_agent.unread_messages += 1

    # Send to tmux session
    sanitized = content.strip()[:500]
    sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', sanitized)  # Strip control chars except newline/tab
    await manager.send_message(to_agent_id, f"[Message from {from_agent.name}]: {sanitized}")

    await hub.broadcast({"type": "agent_message", "message": msg})
    await hub.broadcast({"type": "agent_update", "agent": to_agent.to_dict()})

    return web.json_response(msg, status=201)


async def get_agent_messages(request: web.Request) -> web.Response:
    """GET /api/agents/{id}/messages — get messages for an agent."""
    db: Database = request.app["db"]
    manager: AgentManager = request.app["agent_manager"]
    agent_id = request.match_info["id"]
    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        limit = int(request.query.get("limit", 50))
        offset = int(request.query.get("offset", 0))
    except ValueError:
        return web.json_response({"error": "limit and offset must be integers"}, status=400)

    # Clamp limit to max 1000
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    messages = await db.get_messages_for_agent(agent_id, limit)
    total = await db.get_message_count_for_agent(agent_id)

    # Mark as read
    read_count = await db.mark_messages_read(agent_id)
    if read_count > 0:
        agent.unread_messages = 0
        hub: WebSocketHub = request.app["ws_hub"]
        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})

    return web.json_response({
        "data": messages,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


# ── LLM Summary endpoint ──

async def generate_summary(request: web.Request) -> web.Response:
    """POST /api/agents/{id}/summarize — manually trigger LLM summary."""
    manager: AgentManager = request.app["agent_manager"]
    summarizer: LLMSummarizer | None = request.app.get("llm_summarizer")
    agent_id = request.match_info["id"]
    agent = manager.agents.get(agent_id)

    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)
    if not summarizer:
        return web.json_response({"error": "LLM not configured"}, status=503)

    summary = await summarizer.summarize(
        list(agent.output_lines), agent.task, agent.role, agent.status
    )
    if summary:
        agent.summary = summary
        agent._llm_summary = summary
        hub: WebSocketHub = request.app["ws_hub"]
        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
        return web.json_response({"summary": summary})
    return web.json_response({"error": "LLM summary generation failed", "fallback": agent.summary}, status=503)


# ── History endpoints ──

async def list_history(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        limit = int(request.query.get("limit", 50))
        offset = int(request.query.get("offset", 0))
    except ValueError:
        return web.json_response({"error": "limit and offset must be integers"}, status=400)
    # Clamp limit to max 1000
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    history = await db.get_agent_history(limit, offset)
    total = await db.get_agent_history_count()
    return web.json_response({
        "data": history,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


async def get_history_item(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    agent_id = request.match_info["id"]
    item = await db.get_agent_history_item(agent_id)
    if not item:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(item)


# ── Agent PATCH endpoint (Wave 3A) ──

async def patch_agent(request: web.Request) -> web.Response:
    """PATCH /api/agents/{id} — update agent fields (name, task, project_id)."""
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    agent_id = request.match_info["id"]

    agent = manager.agents.get(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not data:
        return web.json_response({"error": "No fields to update"}, status=400)

    errors = []

    if "name" in data:
        name = data["name"]
        if not isinstance(name, str):
            errors.append("name must be a string")
        else:
            name = re.sub(r'[\x00-\x1f]', '', name).strip()[:100]
            if not name:
                errors.append("name cannot be empty")
            else:
                agent.name = name

    if "task" in data:
        task = data["task"]
        if not isinstance(task, str):
            errors.append("task must be a string")
        elif len(task) > 10000:
            errors.append("task exceeds 10000 character limit")
        else:
            agent.task = task

    if "project_id" in data:
        project_id = data["project_id"]
        if project_id is not None and not isinstance(project_id, str):
            errors.append("project_id must be a string or null")
        else:
            agent.project_id = project_id

    if errors:
        return web.json_response({"error": "; ".join(errors)}, status=400)

    agent.updated_at = datetime.now(timezone.utc).isoformat()
    await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})

    return web.json_response(agent.to_dict())


# ── Bulk operations endpoint (Wave 3B) ──

async def bulk_agent_action(request: web.Request) -> web.Response:
    """POST /api/agents/bulk — perform bulk kill/pause/resume on multiple agents."""
    manager: AgentManager = request.app["agent_manager"]
    hub: WebSocketHub = request.app["ws_hub"]
    db: Database = request.app["db"]

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    action = data.get("action")
    agent_ids = data.get("agent_ids", [])

    if action not in ("kill", "pause", "resume"):
        return web.json_response({"error": f"Invalid action '{action}'. Must be 'kill', 'pause', or 'resume'"}, status=400)

    if not isinstance(agent_ids, list) or not agent_ids:
        return web.json_response({"error": "agent_ids must be a non-empty list"}, status=400)

    success_ids = []
    failed_items = []

    for aid in agent_ids:
        if not isinstance(aid, str):
            failed_items.append({"id": str(aid), "error": "Invalid agent ID type"})
            continue

        agent = manager.agents.get(aid)
        if not agent:
            failed_items.append({"id": aid, "error": "Agent not found"})
            continue

        try:
            if action == "kill":
                # Archive to history before killing
                try:
                    await db.save_agent(agent)
                except Exception as e:
                    log.warning(f"Failed to archive agent {aid} during bulk kill: {e}")
                name = agent.name
                ok = await manager.kill(aid)
                if ok:
                    success_ids.append(aid)
                    await hub.broadcast({"type": "agent_removed", "agent_id": aid})
                    await hub.broadcast({
                        "type": "event", "event": "agent_killed",
                        "agent_id": aid, "message": f"Agent {name} killed (bulk)",
                    })
                else:
                    failed_items.append({"id": aid, "error": "Kill failed"})

            elif action == "pause":
                ok = await manager.pause(aid)
                if ok:
                    success_ids.append(aid)
                    await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                else:
                    failed_items.append({"id": aid, "error": "Pause failed"})

            elif action == "resume":
                ok = await manager.resume(aid)
                if ok:
                    success_ids.append(aid)
                    await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                else:
                    failed_items.append({"id": aid, "error": "Resume failed"})

        except Exception as e:
            failed_items.append({"id": aid, "error": str(e)})

    return web.json_response({"success": success_ids, "failed": failed_items})


# ─────────────────────────────────────────────
# Section 9: Background Tasks
# ─────────────────────────────────────────────

async def output_capture_loop(app: web.Application) -> None:
    """Capture output from all agents every ~1 second."""
    manager: AgentManager = app["agent_manager"]
    hub: WebSocketHub = app["ws_hub"]
    interval = app["config"].output_capture_interval

    while True:
        try:
            for agent_id, agent in list(manager.agents.items()):
                if agent.status in ("paused",):
                    continue

                # Spawn timeout: 30s with no output → error
                if agent.status == "spawning" and agent._spawn_time > 0:
                    if time.monotonic() - agent._spawn_time > 30:
                        agent.status = "error"
                        agent.error_message = "Spawn timeout — no output after 30s"
                        agent._error_entered_at = time.monotonic()
                        agent.updated_at = datetime.now(timezone.utc).isoformat()
                        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                        await hub.broadcast({
                            "type": "event",
                            "event": "agent_error",
                            "agent_id": agent_id,
                            "message": f"Agent {agent.name} spawn timed out",
                        })
                        continue

                # Capture output
                try:
                    new_lines = await manager.capture_output(agent_id)
                    if new_lines:
                        now_mono = time.monotonic()

                        # -- Per-agent metrics tracking --
                        # Time to first output
                        if not agent._first_output_received and agent._spawn_time > 0:
                            agent._first_output_received = True
                            agent.time_to_first_output = round(now_mono - agent._spawn_time, 2)

                        # Track output timestamps and line counts
                        agent.last_output_time = now_mono
                        line_count = len(new_lines)
                        agent.total_output_lines += line_count
                        agent._output_line_timestamps.append((now_mono, line_count))

                        # Calculate rolling output rate (lines per minute over last 60s)
                        cutoff = now_mono - 60.0
                        recent_lines_count = sum(
                            count for ts, count in agent._output_line_timestamps if ts >= cutoff
                        )
                        window = min(60.0, now_mono - agent._spawn_time) if agent._spawn_time > 0 else 60.0
                        agent.output_rate = (recent_lines_count / max(window, 1.0)) * 60.0

                        await hub.broadcast({
                            "type": "agent_output",
                            "agent_id": agent_id,
                            "lines": new_lines,
                        })

                        # Update summary — try LLM first (throttled), fall back to heuristic
                        agent.summary = extract_summary(list(agent.output_lines), agent.task)
                        agent.progress_pct = estimate_progress(agent)

                        # LLM summary with throttling
                        summarizer: LLMSummarizer | None = app.get("llm_summarizer")
                        if summarizer and app["config"].llm_enabled:
                            if now_mono - agent._last_llm_summary_time >= app["config"].llm_summary_interval:
                                agent._last_llm_summary_time = now_mono
                                try:
                                    llm_summary = await summarizer.summarize(
                                        list(agent.output_lines), agent.task,
                                        agent.role, agent.status
                                    )
                                    if llm_summary:
                                        agent.summary = llm_summary
                                        agent._llm_summary = llm_summary
                                except Exception as e:
                                    log.debug(f"LLM summary failed for {agent_id}: {e}")
                except Exception as e:
                    log.warning(f"Output capture error for {agent_id}: {e}")
                    agent.status = "error"
                    agent.error_message = f"Output capture failed: {e}"
                    agent._error_entered_at = time.monotonic()
                    agent.updated_at = datetime.now(timezone.utc).isoformat()
                    await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})

                # Output staleness detection
                if agent.status == "working" and agent.last_output_time > 0:
                    silence = time.monotonic() - agent.last_output_time
                    if silence > 900:  # 15 minutes
                        agent.status = "error"
                        agent.error_message = "No output for 15 minutes"
                        agent._error_entered_at = time.monotonic()
                        agent.updated_at = datetime.now(timezone.utc).isoformat()
                        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                        await hub.broadcast({
                            "type": "event",
                            "event": "agent_error",
                            "agent_id": agent_id,
                            "message": f"Agent {agent.name} stale — no output for 15 minutes",
                        })
                    elif silence > 300:  # 5 minutes
                        await hub.broadcast({
                            "type": "event",
                            "event": "agent_stale_warning",
                            "agent_id": agent_id,
                            "message": f"Agent {agent.name} has had no output for {int(silence)}s",
                        })

                # Update health score
                try:
                    agent.health_score = calculate_health_score(
                        agent, app["config"].memory_limit_mb
                    )
                except Exception:
                    pass

                # Detect status
                try:
                    new_status = await manager.detect_status(agent_id)
                    if new_status != agent.status:
                        old_status = agent.status
                        agent.status = new_status
                        agent.updated_at = datetime.now(timezone.utc).isoformat()
                        log.debug(f"Agent {agent_id} status: {old_status} -> {new_status}")

                        # Track when error status is entered (for auto-restart timing)
                        if new_status == "error" and old_status != "error":
                            agent._error_entered_at = time.monotonic()

                        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})

                        if agent.needs_input:
                            # Debounce: suppress duplicate needs_input events within 5s
                            now_mono = time.monotonic()
                            if now_mono - agent._last_needs_input_event > 5.0:
                                agent._last_needs_input_event = now_mono
                                await hub.broadcast({
                                    "type": "event",
                                    "event": "agent_needs_input",
                                    "agent_id": agent_id,
                                    "message": agent.input_prompt or "Agent needs input",
                                })
                    else:
                        # Broadcast updates even if status unchanged (summary/context may have changed)
                        if new_lines:
                            await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                except Exception as e:
                    log.debug(f"Status detection error for {agent_id}: {e}")

        except Exception as e:
            log.error(f"Output capture loop error: {e}")

        await asyncio.sleep(interval)


async def metrics_loop(app: web.Application) -> None:
    """Collect and broadcast system metrics every 2 seconds."""
    manager: AgentManager = app["agent_manager"]
    hub: WebSocketHub = app["ws_hub"]

    while True:
        try:
            metrics = await collect_system_metrics(manager)

            # Also update per-agent memory
            for agent_id, agent in list(manager.agents.items()):
                try:
                    agent.memory_mb = await manager.get_agent_memory(agent_id)
                except Exception:
                    pass

            await hub.broadcast({"type": "metrics", **metrics.to_dict()})
        except Exception as e:
            log.error(f"Metrics loop error: {e}")

        await asyncio.sleep(2.0)


async def health_check_loop(app: web.Application) -> None:
    """Verify tmux sessions are alive, clean up dead agents, auto-restart crashed agents."""
    manager: AgentManager = app["agent_manager"]
    hub: WebSocketHub = app["ws_hub"]

    while True:
        try:
            for agent_id, agent in list(manager.agents.items()):
                # -- Auto-restart logic for agents in error state --
                if agent.status == "error":
                    now = time.monotonic()
                    error_duration = now - agent._error_entered_at if agent._error_entered_at > 0 else 0

                    # Only attempt restart if error has persisted >10s
                    if error_duration > 10.0 and agent.restart_count < agent.max_restarts:
                        # Exponential backoff: 5s * 2^restart_count (5s, 10s, 20s)
                        backoff = 5.0 * (2 ** agent.restart_count)
                        time_since_last_restart = now - agent.last_restart_time if agent.last_restart_time > 0 else float("inf")

                        if time_since_last_restart >= backoff:
                            log.info(
                                f"Auto-restarting agent {agent_id} ({agent.name}), "
                                f"attempt {agent.restart_count + 1}/{agent.max_restarts}, "
                                f"backoff was {backoff:.0f}s"
                            )
                            try:
                                success = await manager.restart(agent_id)
                                if success:
                                    restarted_agent = manager.agents.get(agent_id)
                                    if restarted_agent:
                                        await hub.broadcast({"type": "agent_update", "agent": restarted_agent.to_dict()})
                                        await hub.broadcast({
                                            "type": "event",
                                            "event": "agent_restarted",
                                            "agent_id": agent_id,
                                            "message": f"Agent {agent.name} auto-restarted (attempt {restarted_agent.restart_count})",
                                        })
                                else:
                                    log.warning(f"Auto-restart failed for agent {agent_id}")
                            except Exception as e:
                                log.error(f"Auto-restart error for {agent_id}: {e}")

                    elif agent.restart_count >= agent.max_restarts and agent._error_entered_at > 0:
                        # Max restarts exhausted — send notification once
                        # Use _error_entered_at as a flag: set to 0 after notification
                        agent._error_entered_at = 0
                        log.warning(
                            f"Agent {agent_id} ({agent.name}) exceeded max restarts "
                            f"({agent.max_restarts}), leaving in error state"
                        )
                        await hub.broadcast({
                            "type": "event",
                            "event": "agent_restart_exhausted",
                            "agent_id": agent_id,
                            "message": (
                                f"Agent {agent.name} failed after {agent.max_restarts} restart attempts. "
                                f"Manual intervention required."
                            ),
                        })
                    continue

                # -- Check if tmux session is still alive --
                exists = await manager._tmux_session_exists(agent.tmux_session)
                if not exists:
                    log.warning(f"Agent {agent_id} ({agent.name}) tmux session died")
                    agent.status = "error"
                    agent.error_message = "tmux session terminated unexpectedly"
                    agent._error_entered_at = time.monotonic()
                    agent.updated_at = datetime.now(timezone.utc).isoformat()
                    await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                    await hub.broadcast({
                        "type": "event",
                        "event": "agent_error",
                        "agent_id": agent_id,
                        "message": f"Agent {agent.name} died unexpectedly",
                    })
                elif agent.pid:
                    # PID liveness check: verify the process is still alive
                    try:
                        os.kill(agent.pid, 0)
                    except ProcessLookupError:
                        # PID is dead but tmux session still exists
                        log.warning(f"Agent {agent_id} ({agent.name}) PID {agent.pid} is dead but tmux session alive")
                        agent.status = "error"
                        agent.error_message = f"Agent process (PID {agent.pid}) died unexpectedly"
                        agent._error_entered_at = time.monotonic()
                        agent.updated_at = datetime.now(timezone.utc).isoformat()
                        await hub.broadcast({"type": "agent_update", "agent": agent.to_dict()})
                        await hub.broadcast({
                            "type": "event",
                            "event": "agent_error",
                            "agent_id": agent_id,
                            "message": f"Agent {agent.name} process died (PID {agent.pid})",
                        })
                    except PermissionError:
                        pass  # Process exists but we can't signal it — that's fine

                # -- Idle agent reaping --
                if agent.status in ("idle", "complete") and agent.last_output_time > 0:
                    idle_duration = time.monotonic() - agent.last_output_time
                    idle_ttl = app["config"].idle_agent_ttl
                    if idle_ttl > 0 and idle_duration > idle_ttl:
                        log.info(f"Reaping idle agent {agent_id} ({agent.name}) — idle for {int(idle_duration)}s")
                        # Archive to history
                        db: Database = app["db"]
                        try:
                            await db.save_agent(agent)
                        except Exception as e:
                            log.warning(f"Failed to archive agent {agent_id} before reaping: {e}")
                        name = agent.name
                        await manager.kill(agent_id)
                        await hub.broadcast({
                            "type": "agent_removed",
                            "agent_id": agent_id,
                            "reason": "idle_timeout",
                        })
                        await hub.broadcast({
                            "type": "event",
                            "event": "agent_reaped",
                            "agent_id": agent_id,
                            "message": f"Agent {name} reaped after {int(idle_duration)}s idle",
                        })
        except Exception as e:
            log.error(f"Health check error: {e}")

        await asyncio.sleep(5.0)


async def memory_watchdog_loop(app: web.Application) -> None:
    """Check per-agent memory usage, warn/kill if over limit."""
    manager: AgentManager = app["agent_manager"]
    hub: WebSocketHub = app["ws_hub"]
    limit = app["config"].memory_limit_mb
    warn_threshold = limit * 0.75

    while True:
        try:
            for agent_id, agent in list(manager.agents.items()):
                if agent.memory_mb > limit:
                    log.warning(f"Agent {agent_id} exceeded memory limit ({agent.memory_mb}MB > {limit}MB), killing")
                    name = agent.name
                    await manager.kill(agent_id)
                    await hub.broadcast({
                        "type": "agent_removed",
                        "agent_id": agent_id,
                    })
                    await hub.broadcast({
                        "type": "event",
                        "event": "agent_killed",
                        "agent_id": agent_id,
                        "message": f"Agent {name} killed: memory limit exceeded ({agent.memory_mb}MB)",
                    })
                elif agent.memory_mb > warn_threshold:
                    log.warning(f"Agent {agent_id} memory warning: {agent.memory_mb}MB / {limit}MB")
        except Exception as e:
            log.error(f"Memory watchdog error: {e}")

        await asyncio.sleep(10.0)


# ─────────────────────────────────────────────
# Section 10: Application Setup & Main
# ─────────────────────────────────────────────

async def start_background_tasks(app: web.Application) -> None:
    # Initialize database with retry
    db: Database = app["db"]
    try:
        await db.init()
    except Exception as e:
        log.error(f"Database init failed: {e}, retrying...")
        await asyncio.sleep(1)
        await db.init()  # Will raise if second attempt fails too

    app["bg_tasks"] = [
        asyncio.create_task(output_capture_loop(app)),
        asyncio.create_task(metrics_loop(app)),
        asyncio.create_task(health_check_loop(app)),
        asyncio.create_task(memory_watchdog_loop(app)),
    ]
    log.info("Background tasks started")


async def cleanup_background_tasks(app: web.Application) -> None:
    for task in app.get("bg_tasks", []):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Archive remaining agents to history
    db: Database = app["db"]
    manager: AgentManager = app["agent_manager"]
    for agent in manager.agents.values():
        try:
            await db.save_agent(agent)
        except Exception:
            pass

    # Close LLM summarizer
    summarizer: LLMSummarizer | None = app.get("llm_summarizer")
    if summarizer:
        await summarizer.close()

    # Close database
    await db.close()

    # Clean up all tmux sessions
    manager.cleanup_all()
    log.info("Cleanup complete")


def create_app(config: Config) -> web.Application:
    app = web.Application()
    app["config"] = config

    manager = AgentManager(config)
    app["agent_manager"] = manager

    db = Database()
    app["db"] = db

    hub = WebSocketHub(manager, config, db)
    app["ws_hub"] = hub

    # LLM Summarizer
    summarizer = LLMSummarizer(config)
    app["llm_summarizer"] = summarizer
    if config.llm_enabled:
        log.info(f"LLM summaries enabled: {config.llm_provider}/{config.llm_model}")
    else:
        log.info("LLM summaries disabled (set XAI_API_KEY and llm.enabled in config)")

    # Routes
    app.router.add_get("/", serve_dashboard)
    app.router.add_get("/logo.png", serve_logo)
    app.router.add_get("/ws", hub.handle_ws)

    # REST API — Agents (bulk + patch BEFORE {id} catch-all routes)
    app.router.add_get("/api/agents", list_agents)
    app.router.add_post("/api/agents", spawn_agent)
    app.router.add_post("/api/agents/bulk", bulk_agent_action)
    app.router.add_patch("/api/agents/{id}", patch_agent)
    app.router.add_get("/api/agents/{id}", get_agent)
    app.router.add_delete("/api/agents/{id}", delete_agent)
    app.router.add_post("/api/agents/{id}/send", send_to_agent)
    app.router.add_post("/api/agents/{id}/pause", pause_agent)
    app.router.add_post("/api/agents/{id}/resume", resume_agent)
    app.router.add_post("/api/agents/{id}/restart", restart_agent)
    app.router.add_get("/api/agents/{id}/output", get_agent_output)
    app.router.add_post("/api/agents/{id}/summarize", generate_summary)
    app.router.add_post("/api/agents/{id}/message", send_agent_message)
    app.router.add_get("/api/agents/{id}/messages", get_agent_messages)

    # REST API — System
    app.router.add_get("/api/system", system_metrics)
    app.router.add_get("/api/roles", list_roles)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/backends", list_backends)

    # REST API — Projects
    app.router.add_get("/api/projects", list_projects)
    app.router.add_post("/api/projects", create_project)
    app.router.add_delete("/api/projects/{id}", delete_project)

    # REST API — Workflows
    app.router.add_get("/api/workflows", list_workflows)
    app.router.add_post("/api/workflows", create_workflow)
    app.router.add_put("/api/workflows/{id}", update_workflow)
    app.router.add_post("/api/workflows/{id}/run", run_workflow)
    app.router.add_delete("/api/workflows/{id}", delete_workflow)

    # REST API — History
    app.router.add_get("/api/history", list_history)
    app.router.add_get("/api/history/{id}", get_history_item)

    # CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })
    for route in list(app.router.routes()):
        try:
            cors.add(route)
        except ValueError:
            pass

    # Background tasks
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    return app


def setup_signal_handlers(agent_manager: AgentManager) -> None:
    def handle_shutdown(signum: int, frame: Any) -> None:
        print("\n\033[33m→ Shutting down Ashlar...\033[0m")
        agent_manager.cleanup_all()
        print("\033[32m✓ All agent sessions cleaned up\033[0m")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)


def main() -> None:
    print_banner()
    has_claude = check_dependencies()
    config = load_config(has_claude)
    setup_logging(config.log_level)

    app = create_app(config)

    # Signal handlers need the agent manager reference
    setup_signal_handlers(app["agent_manager"])

    mode_str = "\033[33mDEMO MODE\033[0m" if config.demo_mode else "\033[32mLIVE MODE\033[0m"
    print(f"  Mode: {mode_str}")
    print(f"  Dashboard: \033[36mhttp://{config.host}:{config.port}\033[0m")
    print(f"  Max agents: {config.max_agents}")
    print()

    web.run_app(app, host=config.host, port=config.port, print=None)


if __name__ == "__main__":
    main()
