"""
Ashlr AO — Git Integration

REST API for git operations: status, diff, log, branches,
stage, unstage, and commit. All operations use async subprocess.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from aiohttp import web

from ashlr_ao.constants import redact_secrets

log = logging.getLogger("ashlr")

# Control characters except newline/tab
_CTRL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


async def _run_git(
    args: list[str], cwd: str, timeout: float = 10.0
) -> tuple[bool, str]:
    """Run a git CLI command. Returns (success, stdout_or_error)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return True, stdout.decode().strip()
        return False, stderr.decode().strip()
    except FileNotFoundError:
        return False, "git CLI not found"
    except asyncio.TimeoutError:
        return False, f"git {' '.join(args[:2])} timed out ({timeout}s)"
    except Exception as e:
        return False, redact_secrets(str(e))


def _validate_repo_path(path: str | None) -> tuple[bool, str]:
    """Validate path is a directory under home/tmp and is a git repo.

    Returns (valid, error_or_resolved_path).
    """
    if not path:
        return False, "Missing required 'path' parameter"

    resolved = os.path.realpath(os.path.expanduser(path))
    home_dir = str(Path.home())
    allowed_prefixes = [home_dir, "/tmp", "/private/tmp"]

    if not any(
        resolved == prefix or resolved.startswith(prefix + os.sep)
        for prefix in allowed_prefixes
    ):
        return False, "Path is outside allowed directories (home or /tmp)"

    if not os.path.isdir(resolved):
        return False, f"Directory not found: {path}"

    # Check for .git directory or git repo
    git_dir = os.path.join(resolved, ".git")
    if not os.path.exists(git_dir):
        return False, f"Not a git repository: {path}"

    return True, resolved


def _validate_relative_paths(files: list[str]) -> str | None:
    """Validate file paths are relative and safe. Returns error or None."""
    for f in files:
        if not f or os.path.isabs(f) or f.startswith("~") or ".." in f.split(os.sep):
            return f"Invalid file path: {f!r} — must be relative with no '..' or '~'"
    return None


def _sanitize_message(message: str) -> str:
    """Strip control characters from commit message."""
    return _CTRL_CHAR_RE.sub("", message).strip()


# ─────────────────────────────────────────────
# Status Parsing
# ─────────────────────────────────────────────


def _parse_porcelain(output: str) -> tuple[str, list[dict]]:
    """Parse `git status --porcelain -b` output.

    Returns (branch_name, list_of_file_dicts).
    """
    branch = ""
    files: list[dict] = []

    for line in output.splitlines():
        if line.startswith("## "):
            # e.g. "## main...origin/main [ahead 2]"
            branch_part = line[3:].split("...")[0].split(" ")[0]
            branch = branch_part
            continue

        if len(line) < 4:
            continue

        index_status = line[0]
        worktree_status = line[1]
        file_path = line[3:]

        # Determine display status and staged flag
        if index_status == "?" and worktree_status == "?":
            files.append({"path": file_path, "status": "??", "staged": False})
        elif index_status == "!" and worktree_status == "!":
            continue  # ignored
        else:
            # Staged changes (index has a status letter)
            if index_status not in (" ", "?", "!"):
                files.append({"path": file_path, "status": index_status, "staged": True})
            # Unstaged changes (worktree has a status letter)
            if worktree_status not in (" ", "?", "!"):
                files.append({"path": file_path, "status": worktree_status, "staged": False})

    return branch, files


# ─────────────────────────────────────────────
# API Handlers
# ─────────────────────────────────────────────


async def git_status(request: web.Request) -> web.Response:
    """GET /api/git/status?path=..."""
    path = request.query.get("path")
    valid, result = _validate_repo_path(path)
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result

    ok, output = await _run_git(["status", "--porcelain", "-b"], repo_path)
    if not ok:
        return web.json_response({"error": output}, status=500)

    branch, files = _parse_porcelain(output)

    # Count ahead/behind
    ahead, behind = 0, 0
    ok2, counts_out = await _run_git(
        ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
        repo_path,
    )
    if ok2 and counts_out:
        parts = counts_out.split()
        if len(parts) == 2:
            try:
                ahead, behind = int(parts[0]), int(parts[1])
            except ValueError:
                ahead, behind = 0, 0

    # Aggregate counts
    counts = {"modified": 0, "added": 0, "deleted": 0, "untracked": 0, "staged": 0}
    for f in files:
        if f["status"] == "??":
            counts["untracked"] += 1
        elif f["status"] == "M":
            counts["modified"] += 1
        elif f["status"] in ("A", "C"):
            counts["added"] += 1
        elif f["status"] == "D":
            counts["deleted"] += 1
        if f["staged"]:
            counts["staged"] += 1

    return web.json_response({
        "branch": branch,
        "files": files,
        "ahead": ahead,
        "behind": behind,
        "clean": len(files) == 0,
        "counts": counts,
    })


async def git_diff(request: web.Request) -> web.Response:
    """GET /api/git/diff?path=...&file=...&staged=false"""
    path = request.query.get("path")
    valid, result = _validate_repo_path(path)
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    file_arg = request.query.get("file")
    staged = request.query.get("staged", "false").lower() == "true"

    args = ["diff"]
    if staged:
        args.append("--staged")
    if file_arg:
        err = _validate_relative_paths([file_arg])
        if err:
            return web.json_response({"error": err}, status=400)
        args.extend(["--", file_arg])

    ok, output = await _run_git(args, repo_path, timeout=15.0)
    if not ok:
        return web.json_response({"error": output}, status=500)

    return web.json_response({
        "diff": output,
        "file": file_arg,
        "staged": staged,
    })


async def git_log(request: web.Request) -> web.Response:
    """GET /api/git/log?path=...&limit=50"""
    path = request.query.get("path")
    valid, result = _validate_repo_path(path)
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    try:
        limit = min(int(request.query.get("limit", "50")), 500)
    except ValueError:
        limit = 50

    fmt = "%H%n%h%n%an%n%ae%n%at%n%s"
    ok, output = await _run_git(
        ["log", f"--format=format:{fmt}", "-n", str(limit)],
        repo_path,
        timeout=15.0,
    )
    if not ok:
        return web.json_response({"error": output}, status=500)

    commits: list[dict] = []
    if output:
        lines = output.split("\n")
        # Each commit is 6 lines
        for i in range(0, len(lines) - 5, 6):
            try:
                commits.append({
                    "hash": lines[i],
                    "short_hash": lines[i + 1],
                    "author": lines[i + 2],
                    "email": lines[i + 3],
                    "timestamp": int(lines[i + 4]),
                    "message": lines[i + 5],
                })
            except (IndexError, ValueError):
                continue

    return web.json_response({"commits": commits})


async def git_branches(request: web.Request) -> web.Response:
    """GET /api/git/branches?path=..."""
    path = request.query.get("path")
    valid, result = _validate_repo_path(path)
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    ok, output = await _run_git(
        ["branch", "-a", "--format=%(refname:short) %(HEAD) %(upstream:short)"],
        repo_path,
    )
    if not ok:
        return web.json_response({"error": output}, status=500)

    current = ""
    branches: list[dict] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        is_current = parts[1] == "*"
        upstream = parts[2] if len(parts) >= 3 else ""
        if is_current:
            current = name
        branches.append({
            "name": name,
            "current": is_current,
            "upstream": upstream,
        })

    return web.json_response({"current": current, "branches": branches})


async def git_stage(request: web.Request) -> web.Response:
    """POST /api/git/stage — body: {"path": "...", "files": [...]}"""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    valid, result = _validate_repo_path(body.get("path"))
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    files = body.get("files", [])
    if not files or not isinstance(files, list):
        return web.json_response({"error": "Missing or empty 'files' list"}, status=400)

    err = _validate_relative_paths(files)
    if err:
        return web.json_response({"error": err}, status=400)

    ok, output = await _run_git(["add", "--"] + files, repo_path)
    if not ok:
        return web.json_response({"error": output}, status=500)

    return web.json_response({"staged": files})


async def git_unstage(request: web.Request) -> web.Response:
    """POST /api/git/unstage — body: {"path": "...", "files": [...]}"""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    valid, result = _validate_repo_path(body.get("path"))
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    files = body.get("files", [])
    if not files or not isinstance(files, list):
        return web.json_response({"error": "Missing or empty 'files' list"}, status=400)

    err = _validate_relative_paths(files)
    if err:
        return web.json_response({"error": err}, status=400)

    ok, output = await _run_git(["restore", "--staged", "--"] + files, repo_path)
    if not ok:
        return web.json_response({"error": output}, status=500)

    return web.json_response({"unstaged": files})


async def git_commit(request: web.Request) -> web.Response:
    """POST /api/git/commit — body: {"path": "...", "message": "..."}"""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    valid, result = _validate_repo_path(body.get("path"))
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    raw_message = body.get("message", "")
    message = _sanitize_message(str(raw_message))

    if not message:
        return web.json_response({"error": "Commit message must not be empty"}, status=400)
    if len(message) > 1000:
        return web.json_response({"error": "Commit message exceeds 1000 characters"}, status=400)

    ok, output = await _run_git(["commit", "-m", message], repo_path)
    if not ok:
        return web.json_response({"error": output}, status=500)

    # Extract commit hash from output
    commit_hash = ""
    ok2, hash_out = await _run_git(["rev-parse", "HEAD"], repo_path)
    if ok2:
        commit_hash = hash_out.strip()

    return web.json_response({
        "success": True,
        "hash": commit_hash,
        "message": message,
    })


async def git_discard(request: web.Request) -> web.Response:
    """POST /api/git/discard — body: {"path": "...", "files": [...]}"""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    valid, result = _validate_repo_path(body.get("path"))
    if not valid:
        return web.json_response({"error": result}, status=400)

    repo_path = result
    files = body.get("files", [])
    if not files or not isinstance(files, list):
        return web.json_response({"error": "Missing or empty 'files' list"}, status=400)

    err = _validate_relative_paths(files)
    if err:
        return web.json_response({"error": err}, status=400)

    # Only discard tracked file changes — untracked files are left alone
    ok, output = await _run_git(["checkout", "--"] + files, repo_path)
    if not ok:
        return web.json_response({"error": output}, status=500)

    return web.json_response({"discarded": files})
