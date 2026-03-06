"""Tests for file browser & editor module (ashlr_ao/files.py)."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch("psutil.cpu_percent", return_value=0.0):
    import ashlr_server

from ashlr_ao.files import (
    _validate_file_path,
    _should_ignore,
    _detect_language,
    _get_git_file_status,
    _get_git_branch,
    _count_visible_children,
    MAX_FILE_SIZE,
)
from tests.conftest import make_test_app


# ── Path Validation ──

class TestValidateFilePath:
    def test_valid_home_path(self):
        home = str(Path.home())
        valid, resolved = _validate_file_path(home)
        assert valid is True
        assert resolved == os.path.realpath(home)

    def test_valid_tmp_path(self):
        valid, resolved = _validate_file_path("/tmp")
        assert valid is True

    def test_rejects_empty(self):
        valid, err = _validate_file_path("")
        assert valid is False
        assert "required" in err.lower()

    def test_rejects_none(self):
        valid, err = _validate_file_path(None)
        assert valid is False

    def test_rejects_outside_home(self):
        valid, err = _validate_file_path("/etc")
        assert valid is False
        assert "home" in err.lower()

    def test_resolves_symlinks(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            link = os.path.join(d, "link")
            os.symlink("/etc", link)
            valid, err = _validate_file_path(link)
            assert valid is False

    def test_tilde_expansion(self):
        valid, resolved = _validate_file_path("~")
        assert valid is True
        assert resolved == os.path.realpath(os.path.expanduser("~"))


# ── Ignore Rules ──

class TestShouldIgnore:
    def test_ignores_git_dir(self):
        assert _should_ignore(".git", True) is True

    def test_ignores_node_modules(self):
        assert _should_ignore("node_modules", True) is True

    def test_ignores_pycache(self):
        assert _should_ignore("__pycache__", True) is True

    def test_ignores_egg_info(self):
        assert _should_ignore("foo.egg-info", True) is True

    def test_allows_normal_dir(self):
        assert _should_ignore("src", True) is False

    def test_ignores_ds_store(self):
        assert _should_ignore(".DS_Store", False) is True

    def test_ignores_pyc(self):
        assert _should_ignore("module.pyc", False) is True

    def test_allows_normal_file(self):
        assert _should_ignore("main.py", False) is False


# ── Language Detection ──

class TestDetectLanguage:
    @pytest.mark.parametrize("ext,lang", [
        (".py", "python"), (".js", "javascript"), (".ts", "typescript"),
        (".html", "html"), (".css", "css"), (".json", "json"),
        (".rs", "rust"), (".go", "go"), (".md", "markdown"),
        (".sh", "bash"), (".unknown", "plaintext"),
    ])
    def test_extensions(self, ext, lang):
        assert _detect_language(f"file{ext}") == lang


# ── Git Helpers ──

class TestGitHelpers:
    @pytest.mark.asyncio
    async def test_get_git_file_status_success(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b" M file.py\n?? new.txt\n", b"")
        )
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await _get_git_file_status("/tmp")
        assert result == {"file.py": "M", "new.txt": "??"}

    @pytest.mark.asyncio
    async def test_get_git_file_status_failure(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 128
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repo"))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await _get_git_file_status("/tmp")
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_git_branch_success(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"main\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await _get_git_branch("/tmp")
        assert result == "main"

    @pytest.mark.asyncio
    async def test_get_git_branch_failure(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await _get_git_branch("/tmp")
        assert result == ""


# ── API Endpoint Tests ──

class TestFileTreeEndpoint:
    @pytest.mark.asyncio
    async def test_tree_missing_path(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.get("/api/files/tree")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_tree_invalid_path(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.get("/api/files/tree?path=/etc")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_tree_valid_path(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            # Create some files
            Path(os.path.join(d, "hello.py")).write_text("print('hi')")
            Path(os.path.join(d, "sub")).mkdir()
            Path(os.path.join(d, "sub", "data.json")).write_text("{}")
            resp = await client.get(f"/api/files/tree?path={d}")
            assert resp.status == 200
            data = await resp.json()
            assert "entries" in data
            names = [e["name"] for e in data["entries"]]
            assert "sub" in names  # dir
            assert "hello.py" in names  # file

    @pytest.mark.asyncio
    async def test_tree_filters_ignored(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(os.path.join(d, "main.py")).write_text("")
            Path(os.path.join(d, ".DS_Store")).write_text("")
            Path(os.path.join(d, "__pycache__")).mkdir()
            resp = await client.get(f"/api/files/tree?path={d}")
            assert resp.status == 200
            data = await resp.json()
            names = [e["name"] for e in data["entries"]]
            assert "main.py" in names
            assert ".DS_Store" not in names
            assert "__pycache__" not in names


class TestFileReadEndpoint:
    @pytest.mark.asyncio
    async def test_read_file(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".py", mode="w", delete=False) as f:
            f.write("x = 42\n")
            f.flush()
            try:
                resp = await client.get(f"/api/files/read?path={f.name}")
                assert resp.status == 200
                data = await resp.json()
                assert data["content"] == "x = 42\n"
                assert data["language"] == "python"
                assert data["line_count"] == 1
            finally:
                os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.get("/api/files/read?path=/tmp/nonexistent_xyz_123.py")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_read_outside_home(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.get("/api/files/read?path=/etc/passwd")
        assert resp.status == 400


class TestFileWriteEndpoint:
    @pytest.mark.asyncio
    async def test_write_file(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            path = os.path.join(d, "new.txt")
            resp = await client.put("/api/files/write", json={
                "path": path, "content": "hello world"
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["written"] is True
            assert Path(path).read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_write_missing_content(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.put("/api/files/write", json={"path": "/tmp/x.txt"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_write_outside_home(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.put("/api/files/write", json={
            "path": "/etc/evil.txt", "content": "hacked"
        })
        assert resp.status == 400


class TestFileCreateEndpoint:
    @pytest.mark.asyncio
    async def test_create_file(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            path = os.path.join(d, "newfile.txt")
            resp = await client.post("/api/files/create", json={"path": path, "type": "file"})
            assert resp.status == 201
            assert os.path.isfile(path)

    @pytest.mark.asyncio
    async def test_create_dir(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            path = os.path.join(d, "newdir")
            resp = await client.post("/api/files/create", json={"path": path, "type": "dir"})
            assert resp.status == 201
            assert os.path.isdir(path)

    @pytest.mark.asyncio
    async def test_create_already_exists(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as f:
            try:
                resp = await client.post("/api/files/create", json={"path": f.name, "type": "file"})
                assert resp.status == 409
            finally:
                os.unlink(f.name)


class TestFileDeleteEndpoint:
    @pytest.mark.asyncio
    async def test_delete_file(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as f:
            resp = await client.delete(f"/api/files/delete?path={f.name}")
            assert resp.status == 200
            assert not os.path.exists(f.name)

    @pytest.mark.asyncio
    async def test_delete_not_found(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.delete("/api/files/delete?path=/tmp/nonexistent_xyz_789")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_delete_nonempty_dir(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(os.path.join(d, "child.txt")).write_text("x")
            resp = await client.delete(f"/api/files/delete?path={d}")
            assert resp.status == 400


class TestFileRenameEndpoint:
    @pytest.mark.asyncio
    async def test_rename_file(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            old = os.path.join(d, "old.txt")
            new = os.path.join(d, "new.txt")
            Path(old).write_text("data")
            resp = await client.post("/api/files/rename", json={
                "old_path": old, "new_path": new
            })
            assert resp.status == 200
            assert os.path.exists(new)
            assert not os.path.exists(old)

    @pytest.mark.asyncio
    async def test_rename_not_found(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/files/rename", json={
            "old_path": "/tmp/nope_xyz", "new_path": "/tmp/nope2_xyz"
        })
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_rename_dest_exists(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            f1 = os.path.join(d, "a.txt")
            f2 = os.path.join(d, "b.txt")
            Path(f1).write_text("a")
            Path(f2).write_text("b")
            resp = await client.post("/api/files/rename", json={
                "old_path": f1, "new_path": f2
            })
            assert resp.status == 409


# ── Edge Cases & Coverage Gaps ──

class TestFileTreeEdgeCases:
    @pytest.mark.asyncio
    async def test_tree_invalid_depth(self, aiohttp_client):
        """Non-integer depth should default to 1, not crash."""
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(os.path.join(d, "file.txt")).write_text("x")
            resp = await client.get(f"/api/files/tree?path={d}&depth=abc")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_tree_depth_clamped(self, aiohttp_client):
        """Depth > 5 should be clamped to 5."""
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            resp = await client.get(f"/api/files/tree?path={d}&depth=100")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_tree_recursive(self, aiohttp_client):
        """Depth=2 returns nested children."""
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            sub = os.path.join(d, "sub")
            os.makedirs(sub)
            Path(os.path.join(sub, "inner.py")).write_text("x")
            resp = await client.get(f"/api/files/tree?path={d}&depth=2")
            assert resp.status == 200
            data = await resp.json()
            dir_entry = [e for e in data["entries"] if e["name"] == "sub"][0]
            assert "children" in dir_entry
            assert dir_entry["children"][0]["name"] == "inner.py"

    @pytest.mark.asyncio
    async def test_tree_not_a_dir(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as f:
            try:
                resp = await client.get(f"/api/files/tree?path={f.name}")
                assert resp.status == 400
            finally:
                os.unlink(f.name)


class TestFileReadEdgeCases:
    @pytest.mark.asyncio
    async def test_read_large_file_rejected(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".txt", delete=False) as f:
            f.write(b"x" * (MAX_FILE_SIZE + 1))
            f.flush()
            try:
                resp = await client.get(f"/api/files/read?path={f.name}")
                assert resp.status == 400
                data = await resp.json()
                assert "too large" in data["error"].lower()
            finally:
                os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_read_binary_file_latin1_fallback(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".bin", delete=False) as f:
            f.write(b"hello \xff\xfe world")
            f.flush()
            try:
                resp = await client.get(f"/api/files/read?path={f.name}")
                assert resp.status == 200
                data = await resp.json()
                assert data["encoding"] == "latin-1"
            finally:
                os.unlink(f.name)


class TestFileWriteEdgeCases:
    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            path = os.path.join(d, "deep", "nested", "file.txt")
            resp = await client.put("/api/files/write", json={
                "path": path, "content": "nested content"
            })
            assert resp.status == 200
            assert Path(path).read_text() == "nested content"

    @pytest.mark.asyncio
    async def test_write_too_large(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.put("/api/files/write", json={
            "path": "/tmp/big.txt", "content": "x" * (MAX_FILE_SIZE + 1)
        })
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_write_invalid_json(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        resp = await client.put("/api/files/write", data="not json",
                               headers={"Content-Type": "application/json"})
        assert resp.status == 400


class TestCountVisibleChildren:
    def test_counts_correctly(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(os.path.join(d, "a.py")).write_text("")
            Path(os.path.join(d, "b.js")).write_text("")
            Path(os.path.join(d, ".DS_Store")).write_text("")
            os.makedirs(os.path.join(d, "__pycache__"))
            os.makedirs(os.path.join(d, "src"))
            children = os.listdir(d)
            count = _count_visible_children(d, children)
            # a.py, b.js, src = 3 visible (excludes .DS_Store, __pycache__)
            assert count == 3


class TestGitStatusInFileTree:
    @pytest.mark.asyncio
    async def test_git_rename_handling(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b"R  old.py -> new.py\n", b"")
        )
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await _get_git_file_status("/tmp")
        assert "new.py" in result
        assert "old.py" not in result


# ── Route Registration ──

class TestFileRouteRegistration:
    def test_file_routes_registered(self):
        app = make_test_app()
        routes = set()
        for r in app.router.routes():
            info = r.get_info()
            routes.add(info.get("formatter", info.get("path", "")))
        expected = [
            "/api/files/tree", "/api/files/read", "/api/files/write",
            "/api/files/create", "/api/files/delete", "/api/files/rename",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"


class TestSymlinkSafety:
    @pytest.mark.asyncio
    async def test_tree_skips_symlinks_outside_allowed(self, aiohttp_client):
        """Symlinks pointing outside home/tmp are excluded from tree."""
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            # Create a normal file
            Path(os.path.join(d, "good.txt")).write_text("hello")
            # Create symlink to /usr (outside allowed dirs)
            try:
                os.symlink("/usr", os.path.join(d, "escape_link"))
            except OSError:
                pytest.skip("Cannot create symlinks")
            resp = await client.get(f"/api/files/tree?path={d}")
            assert resp.status == 200
            data = await resp.json()
            names = [e["name"] for e in data["entries"]]
            assert "good.txt" in names
            assert "escape_link" not in names

    @pytest.mark.asyncio
    async def test_tree_allows_symlinks_within_tmp(self, aiohttp_client):
        """Symlinks within allowed directories are included."""
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            subdir = os.path.join(d, "sub")
            os.makedirs(subdir)
            Path(os.path.join(subdir, "file.txt")).write_text("hello")
            try:
                os.symlink(subdir, os.path.join(d, "safe_link"))
            except OSError:
                pytest.skip("Cannot create symlinks")
            resp = await client.get(f"/api/files/tree?path={d}")
            assert resp.status == 200
            data = await resp.json()
            names = [e["name"] for e in data["entries"]]
            assert "safe_link" in names


class TestFileWriteErrorHandling:
    @pytest.mark.asyncio
    async def test_write_permission_denied(self, aiohttp_client):
        """Write to unwritable path returns 403."""
        app = make_test_app()
        client = await aiohttp_client(app)
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            path = os.path.join(d, "readonly.txt")
            Path(path).write_text("original")
            os.chmod(path, 0o444)
            try:
                resp = await client.put("/api/files/write", json={
                    "path": path, "content": "new content"
                })
                assert resp.status == 403
            finally:
                os.chmod(path, 0o644)
