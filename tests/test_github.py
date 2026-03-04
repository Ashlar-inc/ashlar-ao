"""Tests for GitHub integration endpoints and _run_gh helper."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

with patch("psutil.cpu_percent", return_value=0.0):
    import ashlr_server
    import ashlr_ao.server as _server_mod

from conftest import make_mock_db as _make_mock_db, make_test_app as _make_test_app

# Patch target must be the actual module where the handler resolves _run_gh
_GH_PATCH = "ashlr_ao.server._run_gh"


@pytest.fixture
async def cli(aiohttp_client):
    """Create a test client for the Ashlr app."""
    app = _make_test_app()
    return await aiohttp_client(app)


# ─────────────────────────────────────────────
# _run_gh helper
# ─────────────────────────────────────────────


class TestRunGh:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"output text\n", b"")
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, out = await ashlr_server._run_gh(["auth", "status"])
        assert ok is True
        assert out == "output text"

    @pytest.mark.asyncio
    async def test_failure_returns_stderr(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"not authenticated\n")
        mock_proc.returncode = 1
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, out = await ashlr_server._run_gh(["auth", "status"])
        assert ok is False
        assert "not authenticated" in out

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            ok, out = await ashlr_server._run_gh(["--version"])
        assert ok is False
        assert "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            ok, out = await ashlr_server._run_gh(["repo", "view"], timeout=0.1)
        assert ok is False
        assert "timed out" in out.lower()

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("broken")):
            ok, out = await ashlr_server._run_gh(["pr", "list"])
        assert ok is False
        assert "broken" in out

    @pytest.mark.asyncio
    async def test_passes_cwd(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await ashlr_server._run_gh(["status"], cwd="/tmp/repo")
        mock_exec.assert_called_once()
        assert mock_exec.call_args.kwargs.get("cwd") == "/tmp/repo"


# ─────────────────────────────────────────────
# GET /api/github/status
# ─────────────────────────────────────────────


class TestGithubStatus:
    @pytest.mark.asyncio
    async def test_fully_authenticated(self, cli):
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (True, "Logged in to github.com")
            resp = await cli.get("/api/github/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["available"] is True
        assert data["authenticated"] is True

    @pytest.mark.asyncio
    async def test_installed_but_not_authed(self, cli):
        async def side_effect(args, **kw):
            if "auth" in args:
                return (False, "not logged in")
            return (True, "gh version 2.40.0")

        with patch(_GH_PATCH, side_effect=side_effect):
            resp = await cli.get("/api/github/status")
        data = await resp.json()
        assert data["available"] is True
        assert data["authenticated"] is False

    @pytest.mark.asyncio
    async def test_not_installed(self, cli):
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (False, "gh CLI not found")
            resp = await cli.get("/api/github/status")
        data = await resp.json()
        assert data["available"] is False
        assert data["authenticated"] is False


# ─────────────────────────────────────────────
# GET /api/projects/{id}/github
# ─────────────────────────────────────────────


class TestGithubProjectInfo:
    @pytest.mark.asyncio
    async def test_project_not_found(self, cli):
        resp = await cli.get("/api/projects/nonexistent/github")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_project_path_not_on_disk(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/nonexistent/nowhere"})
        resp = await cli.get("/api/projects/p1/github")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_not_a_github_repo(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (False, "not a git repository")
            resp = await cli.get("/api/projects/p1/github")
        assert resp.status == 400
        data = await resp.json()
        assert "not a GitHub repository" in data["error"].lower() or "not a github" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_full_info_returned(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        repo_json = json.dumps({"nameWithOwner": "user/repo", "url": "https://github.com/user/repo"})
        pr_json = json.dumps([{"number": 1, "title": "Fix bug"}])
        issue_json = json.dumps([{"number": 5, "title": "Feature request"}])
        branch_output = "main\ndev\nfeature/auth"

        call_count = 0

        async def side_effect(args, **kw):
            nonlocal call_count
            call_count += 1
            if "repo" in args and "view" in args:
                return (True, repo_json)
            if "pr" in args:
                return (True, pr_json)
            if "issue" in args:
                return (True, issue_json)
            if "api" in args:
                return (True, branch_output)
            return (False, "unknown")

        with patch(_GH_PATCH, side_effect=side_effect):
            resp = await cli.get("/api/projects/p1/github")
        assert resp.status == 200
        data = await resp.json()
        assert data["repo"]["nameWithOwner"] == "user/repo"
        assert len(data["pull_requests"]) == 1
        assert len(data["issues"]) == 1
        assert "main" in data["branches"]
        assert call_count == 4  # repo + PRs + issues + branches


# ─────────────────────────────────────────────
# POST /api/projects/{id}/github/issues
# ─────────────────────────────────────────────


class TestGithubCreateIssue:
    @pytest.mark.asyncio
    async def test_project_not_found(self, cli):
        resp = await cli.post("/api/projects/nope/github/issues", json={"title": "Bug"})
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_missing_title(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        resp = await cli.post("/api/projects/p1/github/issues", json={"title": ""})
        assert resp.status == 400
        data = await resp.json()
        assert "title" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_invalid_json(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        resp = await cli.post("/api/projects/p1/github/issues", data=b"not json",
                              headers={"Content-Type": "application/json"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_success(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (True, "https://github.com/user/repo/issues/42")
            resp = await cli.post("/api/projects/p1/github/issues",
                                  json={"title": "Bug report", "body": "Details here", "labels": ["bug"]})
        assert resp.status == 201
        data = await resp.json()
        assert "42" in data["url"]

    @pytest.mark.asyncio
    async def test_gh_failure(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (False, "permission denied")
            resp = await cli.post("/api/projects/p1/github/issues", json={"title": "Bug"})
        assert resp.status == 400


# ─────────────────────────────────────────────
# POST /api/projects/{id}/github/pulls
# ─────────────────────────────────────────────


class TestGithubCreatePR:
    @pytest.mark.asyncio
    async def test_project_not_found(self, cli):
        resp = await cli.post("/api/projects/nope/github/pulls", json={"title": "PR"})
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_missing_title(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        resp = await cli.post("/api/projects/p1/github/pulls", json={"title": ""})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_json(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        resp = await cli.post("/api/projects/p1/github/pulls", data=b"not json",
                              headers={"Content-Type": "application/json"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_success_with_all_fields(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (True, "https://github.com/user/repo/pull/7")
            resp = await cli.post("/api/projects/p1/github/pulls",
                                  json={"title": "Add feature", "body": "PR body",
                                        "head": "feature/x", "base": "main"})
        assert resp.status == 201
        data = await resp.json()
        assert "7" in data["url"]
        # Verify all args passed correctly
        call_args = mock_gh.call_args[0][0]
        assert "--head" in call_args
        assert "--base" in call_args

    @pytest.mark.asyncio
    async def test_success_minimal(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (True, "https://github.com/user/repo/pull/8")
            resp = await cli.post("/api/projects/p1/github/pulls", json={"title": "Quick fix"})
        assert resp.status == 201
        # Minimal args — no head/base/body
        call_args = mock_gh.call_args[0][0]
        assert "--head" not in call_args
        assert "--base" not in call_args
        assert "--body" not in call_args

    @pytest.mark.asyncio
    async def test_gh_failure(self, cli):
        cli.app["db"].get_project = AsyncMock(return_value={"id": "p1", "path": "/tmp"})
        with patch(_GH_PATCH, new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = (False, "no commits between main and main")
            resp = await cli.post("/api/projects/p1/github/pulls", json={"title": "PR"})
        assert resp.status == 400
