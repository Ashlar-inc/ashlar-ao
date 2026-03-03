"""
Ashlr AO — Extension Discovery

Scans for Claude Code skills, MCP servers, and plugins from the filesystem.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ashlr_ao.constants import log


@dataclass
class SkillInfo:
    """A Claude Code slash-command skill discovered from filesystem."""
    name: str              # e.g. "commit" or "gsd/add-phase"
    description: str
    source: str            # "user" (global) or "project"
    file_path: str         # absolute path to the .md file
    argument_hint: str = ""
    allowed_tools: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "file_path": self.file_path,
            "argument_hint": self.argument_hint,
            "allowed_tools": self.allowed_tools,
        }


@dataclass
class MCPServerInfo:
    """An MCP server discovered from settings.json or .mcp.json."""
    name: str
    server_type: str       # "stdio" | "http" | "sse" | "unknown"
    url_or_command: str    # URL for http/sse, command for stdio
    source: str            # "user" (global) or project path
    args: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.server_type,
            "url_or_command": self.url_or_command,
            "source": self.source,
            "args": self.args,
        }


@dataclass
class PluginInfo:
    """A Claude Code plugin discovered from settings.json."""
    name: str              # e.g. "frontend-design@claude-plugins-official"
    provider: str          # e.g. "claude-plugins-official"
    enabled: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "enabled": self.enabled,
        }


class ExtensionScanner:
    """Scans the filesystem for Claude Code skills, MCP servers, and plugins.
    Results are cached in memory and refreshed on demand."""

    def __init__(self) -> None:
        self.skills: list[SkillInfo] = []
        self.mcp_servers: list[MCPServerInfo] = []
        self.plugins: list[PluginInfo] = []
        self._scanned_at: str = ""

    def to_dict(self) -> dict:
        return {
            "skills": [s.to_dict() for s in self.skills],
            "mcp_servers": [m.to_dict() for m in self.mcp_servers],
            "plugins": [p.to_dict() for p in self.plugins],
            "scanned_at": self._scanned_at,
        }

    def scan(self, project_dirs: list[str] | None = None) -> dict:
        """Full filesystem scan. Returns to_dict() result."""
        self.skills = self._scan_skills(project_dirs or [])
        self.mcp_servers = self._scan_mcp_servers(project_dirs or [])
        self.plugins = self._scan_plugins()
        self._scanned_at = datetime.now(timezone.utc).isoformat()
        log.info(
            f"Extension scan: {len(self.skills)} skills, "
            f"{len(self.mcp_servers)} MCP servers, {len(self.plugins)} plugins"
        )
        return self.to_dict()

    def _scan_skills(self, project_dirs: list[str]) -> list[SkillInfo]:
        """Scan for .md skill files in user global + project dirs."""
        skills: list[SkillInfo] = []
        # User global: ~/.claude/commands/**/*.md
        global_dir = Path.home() / ".claude" / "commands"
        if global_dir.is_dir():
            skills.extend(self._scan_skill_dir(global_dir, "user"))
        # Per-project: {project}/.claude/commands/**/*.md
        for pdir in project_dirs:
            proj_cmd_dir = Path(pdir) / ".claude" / "commands"
            if proj_cmd_dir.is_dir():
                skills.extend(self._scan_skill_dir(proj_cmd_dir, pdir))
        return skills

    def _scan_skill_dir(self, base_dir: Path, source: str) -> list[SkillInfo]:
        """Scan a single commands directory for .md skill files."""
        results: list[SkillInfo] = []
        try:
            for md_file in sorted(base_dir.rglob("*.md")):
                if not md_file.is_file():
                    continue
                # Build skill name: relative to base_dir, without extension
                rel = md_file.relative_to(base_dir)
                name = str(rel.with_suffix(""))  # e.g. "commit" or "gsd/add-phase"
                # Parse YAML frontmatter
                desc, arg_hint, allowed = self._parse_skill_frontmatter(md_file)
                results.append(SkillInfo(
                    name=name,
                    description=desc,
                    source=source,
                    file_path=str(md_file),
                    argument_hint=arg_hint,
                    allowed_tools=allowed,
                ))
        except Exception as e:
            log.warning(f"Error scanning skills in {base_dir}: {e}")
        return results

    @staticmethod
    def _parse_skill_frontmatter(path: Path) -> tuple[str, str, str]:
        """Parse YAML frontmatter from a skill .md file.
        Returns (description, argument_hint, allowed_tools)."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ("", "", "")
        if not text.startswith("---"):
            return ("", "", "")
        end = text.find("---", 3)
        if end < 0:
            return ("", "", "")
        frontmatter = text[3:end].strip()
        try:
            meta = yaml.safe_load(frontmatter) or {}
        except Exception:
            return ("", "", "")
        desc = str(meta.get("description", ""))
        arg_hint = str(meta.get("argument-hint", ""))
        allowed = str(meta.get("allowed-tools", ""))
        return (desc, arg_hint, allowed)

    def _scan_mcp_servers(self, project_dirs: list[str]) -> list[MCPServerInfo]:
        """Scan for MCP server configurations."""
        servers: list[MCPServerInfo] = []
        # Global: ~/.claude/settings.json → mcpServers
        settings_path = Path.home() / ".claude" / "settings.json"
        if settings_path.is_file():
            servers.extend(self._parse_mcp_from_settings(settings_path, "user"))
        # Per-project: {project}/.mcp.json
        for pdir in project_dirs:
            mcp_path = Path(pdir) / ".mcp.json"
            if mcp_path.is_file():
                servers.extend(self._parse_mcp_from_file(mcp_path, pdir))
        return servers

    def _parse_mcp_from_settings(self, path: Path, source: str) -> list[MCPServerInfo]:
        """Parse mcpServers from a settings.json file."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mcp_section = data.get("mcpServers", {})
            return self._parse_mcp_dict(mcp_section, source)
        except Exception as e:
            log.warning(f"Error parsing MCP from {path}: {e}")
            return []

    def _parse_mcp_from_file(self, path: Path, source: str) -> list[MCPServerInfo]:
        """Parse an .mcp.json file."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mcp_section = data.get("mcpServers", data)
            return self._parse_mcp_dict(mcp_section, source)
        except Exception as e:
            log.warning(f"Error parsing MCP from {path}: {e}")
            return []

    @staticmethod
    def _parse_mcp_dict(mcp_dict: dict, source: str) -> list[MCPServerInfo]:
        """Convert a mcpServers dict to list of MCPServerInfo."""
        results: list[MCPServerInfo] = []
        if not isinstance(mcp_dict, dict):
            return results
        for name, cfg in mcp_dict.items():
            if not isinstance(cfg, dict):
                continue
            # Determine type
            stype = cfg.get("type", "unknown")
            if stype == "stdio":
                url_or_cmd = cfg.get("command", "")
                args = cfg.get("args", [])
            elif stype in ("http", "sse"):
                url_or_cmd = cfg.get("url", "")
                args = []
            else:
                url_or_cmd = cfg.get("command", cfg.get("url", ""))
                args = cfg.get("args", [])
            results.append(MCPServerInfo(
                name=name,
                server_type=stype,
                url_or_command=url_or_cmd,
                source=source,
                args=args if isinstance(args, list) else [],
            ))
        return results

    def _scan_plugins(self) -> list[PluginInfo]:
        """Scan for enabled plugins from settings.json."""
        settings_path = Path.home() / ".claude" / "settings.json"
        if not settings_path.is_file():
            return []
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            plugins_section = data.get("enabledPlugins", {})
            if not isinstance(plugins_section, dict):
                return []
            results: list[PluginInfo] = []
            for full_name, enabled in plugins_section.items():
                # Parse "name@provider" format
                parts = full_name.split("@", 1)
                short_name = parts[0]
                provider = parts[1] if len(parts) > 1 else "unknown"
                results.append(PluginInfo(
                    name=short_name,
                    provider=provider,
                    enabled=bool(enabled),
                ))
            return results
        except Exception as e:
            log.warning(f"Error scanning plugins: {e}")
            return []
