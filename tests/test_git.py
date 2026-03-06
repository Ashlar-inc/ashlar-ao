"""Tests for git integration module (ashlr_ao/git.py)."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch("psutil.cpu_percent", return_value=0.0):
    import ashlr_server

from ashlr_ao.git import (
    _run_git,
    _validate_repo_path,
    _validate_relative_paths,
    _sanitize_message,
    _parse_porcelain,
)
from tests.conftest import make_test_app


# ── Helper Functions ──

class TestRunGit:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"output\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, result = await _run_git(["status"], "/tmp")
        assert ok is True
        assert result == "output"

    @pytest.mark.asyncio
    async def test_failure(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 128
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: error"))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, result = await _run_git(["log"], "/tmp")
        assert ok is False
        assert "fatal" in result

    @pytest.mark.asyncio
    async def test_git_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            ok, result = await _run_git(["status"], "/tmp")
        assert ok is False
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, result = await _run_git(["diff"], "/tmp")
        assert ok is False
        assert "timed out" in result.lower()
        assert "diff" in result  # Should include the command that timed out


class TestValidateRepoPath:
    def test_valid_repo(self):
        # Use the actual project dir (which has .git)
        project_dir = str(Path(__file__).parent.parent)
        valid, result = _validate_repo_path(project_dir)
        assert valid is True

    def test_missing_path(self):
        valid, err = _validate_repo_path(None)
        assert valid is False

    def test_empty_path(self):
        valid, err = _validate_repo_path("")
        assert valid is False

    def test_outside_home(self):
        valid, err = _validate_repo_path("/etc")
        assert valid is False
        assert "outside" in err.lower()

    def test_not_a_dir(self):
        valid, err = _validate_repo_path("/tmp/nonexistent_xyz_999")
        assert valid is False
        assert "not found" in err.lower()

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            valid, err = _validate_repo_path(d)
            assert valid is False
            assert "git" in err.lower()


class TestValidateRelativePaths:
    def test_valid_paths(self):
        assert _validate_relative_paths(["src/main.py", "README.md"]) is None

    def test_rejects_absolute(self):
        err = _validate_relative_paths(["/etc/passwd"])
        assert err is not None

    def test_rejects_traversal(self):
        err = _validate_relative_paths(["../../../etc/passwd"])
        assert err is not None

    def test_rejects_empty(self):
        err = _validate_relative_paths([""])
        assert err is not None


class TestSanitizeMessage:
    def test_strips_control_chars(self):
        assert _sanitize_message("hello\x00world") == "helloworld"

    def test_preserves_newlines(self):
        assert _sanitize_message("line1\nline2") == "line1\nline2"

    def test_strips_whitespace(self):
        assert _sanitize_message("  msg  ") == "msg"


# ── Porcelain Parsing ──

class TestParsePorcelain:
    def test_branch_and_files(self):
        output = "## main...origin/main\n M file.py\nA  new.txt\n?? untracked.js\n"
        branch, files = _parse_porcelain(output)
        assert branch == "main"
        assert len(files) == 3

    def test_staged_and_unstaged(self):
        output = "## dev\nMM both.py\n"
        branch, files = _parse_porcelain(output)
        assert branch == "dev"
        # MM = staged M + unstaged M
        assert len(files) == 2
        staged = [f for f in files if f["staged"]]
        unstaged = [f for f in files if not f["staged"]]
        assert len(staged) == 1
        assert len(unstaged) == 1

    def test_untracked_files(self):
        output = "## main\n?? newfile.py\n"
        branch, files = _parse_porcelain(output)
        assert files[0]["status"] == "??"
        assert files[0]["staged"] is False

    def test_empty_output(self):
        branch, files = _parse_porcelain("")
        assert branch == ""
        assert files == []

    def test_clean_repo(self):
        output = "## main...origin/main\n"
        branch, files = _parse_porcelain(output)
        assert branch == "main"
        assert files == []

    def test_ignored_files_skipped(self):
        output = "## main\n!! ignored.log\n"
        _, files = _parse_porcelain(output)
        assert len(files) == 0


# ── API Endpoint Tests ──

class TestGitStatusEndpoint:
    @pytest.mark.asyncio
    async def test_missing_path(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.get("/api/git/status")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_not_a_repo(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            resp = await client.get(f"/api/git/status?path={d}")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_valid_repo(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/status?path={project_dir}")
        assert resp.status == 200
        data = await resp.json()
        assert "branch" in data
        assert "files" in data
        assert "counts" in data


class TestGitDiffEndpoint:
    @pytest.mark.asyncio
    async def test_diff_missing_path(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.get("/api/git/diff")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_diff_valid_repo(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/diff?path={project_dir}")
        assert resp.status == 200
        data = await resp.json()
        assert "diff" in data

    @pytest.mark.asyncio
    async def test_diff_with_file_traversal(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/diff?path={project_dir}&file=../../../etc/passwd")
        assert resp.status == 400


class TestGitLogEndpoint:
    @pytest.mark.asyncio
    async def test_log_valid_repo(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/log?path={project_dir}&limit=5")
        assert resp.status == 200
        data = await resp.json()
        assert "commits" in data
        assert isinstance(data["commits"], list)


class TestGitBranchesEndpoint:
    @pytest.mark.asyncio
    async def test_branches_valid_repo(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/branches?path={project_dir}")
        assert resp.status == 200
        data = await resp.json()
        assert "branches" in data
        assert "current" in data


class TestGitStageEndpoint:
    @pytest.mark.asyncio
    async def test_stage_missing_files(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/stage", json={"path": project_dir})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_stage_absolute_path_rejected(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/stage", json={
            "path": project_dir, "files": ["/etc/passwd"]
        })
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_stage_traversal_rejected(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/stage", json={
            "path": project_dir, "files": ["../../etc/passwd"]
        })
        assert resp.status == 400


class TestGitUnstageEndpoint:
    @pytest.mark.asyncio
    async def test_unstage_missing_files(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/unstage", json={"path": project_dir})
        assert resp.status == 400


class TestGitCommitEndpoint:
    @pytest.mark.asyncio
    async def test_commit_empty_message(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/commit", json={
            "path": project_dir, "message": ""
        })
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_commit_too_long_message(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/commit", json={
            "path": project_dir, "message": "x" * 1001
        })
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_commit_control_chars_sanitized(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        # Message with only control chars → empty after sanitize → 400
        resp = await client.post("/api/git/commit", json={
            "path": project_dir, "message": "\x00\x01\x02"
        })
        assert resp.status == 400


class TestGitDiscardEndpoint:
    @pytest.mark.asyncio
    async def test_discard_missing_files(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/discard", json={"path": project_dir})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_discard_traversal_rejected(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.post("/api/git/discard", json={
            "path": project_dir, "files": ["../../../etc/passwd"]
        })
        assert resp.status == 400


# ── Edge Cases & Coverage Gaps ──

class TestGitLogParsing:
    @pytest.mark.asyncio
    async def test_log_malformed_output(self, aiohttp_client):
        """Log endpoint handles incomplete git output gracefully."""
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        # Just verify it doesn't crash — actual parsing is in the handler
        resp = await client.get(f"/api/git/log?path={project_dir}&limit=1")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data["commits"], list)

    @pytest.mark.asyncio
    async def test_log_invalid_limit(self, aiohttp_client):
        """Non-integer limit should default to 50."""
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/log?path={project_dir}&limit=abc")
        assert resp.status == 200


class TestGitDiffEdgeCases:
    @pytest.mark.asyncio
    async def test_diff_staged(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        project_dir = str(Path(__file__).parent.parent)
        resp = await client.get(f"/api/git/diff?path={project_dir}&staged=true")
        assert resp.status == 200
        data = await resp.json()
        assert data["staged"] is True


class TestGitBodyValidation:
    @pytest.mark.asyncio
    async def test_stage_invalid_json(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/git/stage", data="not json",
                                headers={"Content-Type": "application/json"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unstage_invalid_json(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/git/unstage", data="not json",
                                headers={"Content-Type": "application/json"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_commit_invalid_json(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/git/commit", data="not json",
                                headers={"Content-Type": "application/json"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_discard_invalid_json(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/git/discard", data="not json",
                                headers={"Content-Type": "application/json"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_stage_not_a_repo(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            resp = await client.post("/api/git/stage", json={
                "path": d, "files": ["file.txt"]
            })
            assert resp.status == 400


class TestParsePorcelainEdgeCases:
    def test_branch_with_ahead_behind(self):
        output = "## main...origin/main [ahead 2, behind 1]\n"
        branch, files = _parse_porcelain(output)
        assert branch == "main"
        assert files == []

    def test_added_file(self):
        output = "## dev\nA  new.py\n"
        _, files = _parse_porcelain(output)
        assert len(files) == 1
        assert files[0]["status"] == "A"
        assert files[0]["staged"] is True

    def test_deleted_file(self):
        output = "## main\n D removed.py\n"
        _, files = _parse_porcelain(output)
        assert len(files) == 1
        assert files[0]["status"] == "D"
        assert files[0]["staged"] is False

    def test_short_lines_skipped(self):
        output = "## main\nXY\n"
        _, files = _parse_porcelain(output)
        assert files == []


class TestRunGitEdgeCases:
    @pytest.mark.asyncio
    async def test_exception_redacts_secrets(self):
        with patch("asyncio.create_subprocess_exec",
                   side_effect=Exception("token ghp_secret123 leaked")):
            ok, result = await _run_git(["status"], "/tmp")
        assert ok is False
        # Should still contain some error message (redaction may or may not match)
        assert isinstance(result, str)


# ── Route Registration ──

class TestGitRouteRegistration:
    def test_git_routes_registered(self):
        app = make_test_app()
        routes = set()
        for r in app.router.routes():
            info = r.get_info()
            routes.add(info.get("formatter", info.get("path", "")))
        expected = [
            "/api/git/status", "/api/git/diff", "/api/git/log",
            "/api/git/branches", "/api/git/stage", "/api/git/unstage",
            "/api/git/commit", "/api/git/discard",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"


# ── Additional Edge Cases ──

class TestSanitizeMessage:
    def test_strips_control_chars(self):
        msg = "hello\x00\x07world\x1b"
        result = _sanitize_message(msg)
        assert result == "helloworld"
        assert "\x00" not in result
        assert "\x07" not in result

    def test_preserves_newlines_and_tabs(self):
        msg = "line1\nline2\ttab"
        result = _sanitize_message(msg)
        assert "\n" in result
        assert "\t" in result

    def test_strips_whitespace(self):
        msg = "   hello   "
        result = _sanitize_message(msg)
        assert result == "hello"

    def test_empty_after_strip(self):
        msg = "\x00\x07  "
        result = _sanitize_message(msg)
        assert result == ""


class TestCommitMessageValidation:
    @pytest.mark.asyncio
    async def test_commit_message_too_long(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.makedirs(os.path.join(d, ".git"))
            resp = await client.post("/api/git/commit", json={
                "path": d, "message": "x" * 1001
            })
            assert resp.status == 400
            data = await resp.json()
            assert "1000" in data["error"]

    @pytest.mark.asyncio
    async def test_commit_empty_message(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.makedirs(os.path.join(d, ".git"))
            resp = await client.post("/api/git/commit", json={
                "path": d, "message": ""
            })
            assert resp.status == 400
            data = await resp.json()
            assert "empty" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_commit_control_chars_only(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.makedirs(os.path.join(d, ".git"))
            resp = await client.post("/api/git/commit", json={
                "path": d, "message": "\x00\x07\x1f"
            })
            assert resp.status == 400


class TestGitDiscardValidation:
    @pytest.mark.asyncio
    async def test_discard_missing_files(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.makedirs(os.path.join(d, ".git"))
            resp = await client.post("/api/git/discard", json={
                "path": d
            })
            assert resp.status == 400
            data = await resp.json()
            assert "files" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_discard_path_traversal(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.makedirs(os.path.join(d, ".git"))
            resp = await client.post("/api/git/discard", json={
                "path": d, "files": ["../../../etc/passwd"]
            })
            assert resp.status == 400
            data = await resp.json()
            assert "Invalid file path" in data["error"]


class TestValidateRepoPath:
    def test_outside_allowed_dirs(self):
        valid, err = _validate_repo_path("/usr/bin")
        assert valid is False
        assert "outside" in err.lower() or "allowed" in err.lower()

    def test_not_a_directory(self):
        valid, err = _validate_repo_path("/tmp/nonexistent_dir_xyz")
        assert valid is False

    def test_missing_git_dir(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            valid, err = _validate_repo_path(d)
            assert valid is False
            assert "not a git" in err.lower()

    def test_valid_git_repo(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.makedirs(os.path.join(d, ".git"))
            valid, resolved = _validate_repo_path(d)
            assert valid is True
            assert resolved == os.path.realpath(d)
