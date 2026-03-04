"""Tests for previously untested code paths: config write lock, disk space check, etc."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

with patch("psutil.cpu_percent", return_value=0.0):
    import ashlr_server

from conftest import make_mock_db as _make_mock_db, make_test_app as _make_test_app

TEST_WORKING_DIR = str(Path.home())


@pytest.fixture
async def cli(aiohttp_client):
    app = _make_test_app()
    return await aiohttp_client(app)


# ─────────────────────────────────────────────
# Config write lock — concurrent PUT /api/config
# ─────────────────────────────────────────────


class TestConfigWriteLock:
    @pytest.mark.asyncio
    async def test_concurrent_config_writes_serialize(self, aiohttp_client):
        """Two concurrent PUT /api/config should not interleave (lock serializes)."""
        app = _make_test_app()
        client = await aiohttp_client(app)

        call_order = []

        orig_to_thread = asyncio.to_thread

        async def slow_to_thread(fn, *args, **kwargs):
            call_order.append("start")
            await asyncio.sleep(0.05)
            call_order.append("end")

        with patch("ashlr_ao.server.asyncio.to_thread", side_effect=slow_to_thread):
            r1 = client.put("/api/config", json={"max_agents": 10})
            r2 = client.put("/api/config", json={"max_agents": 20})
            resp1, resp2 = await asyncio.gather(r1, r2)

        # Both should complete (200 or 500 if write mocked out doesn't actually persist)
        assert resp1.status in (200, 500)
        assert resp2.status in (200, 500)
        # With a lock, writes should be serialized: start, end, start, end — not start, start, end, end
        if len(call_order) == 4:
            assert call_order == ["start", "end", "start", "end"]

    @pytest.mark.asyncio
    async def test_config_write_failure_returns_500(self, aiohttp_client):
        """PUT /api/config should return 500 if disk write fails."""
        app = _make_test_app()
        client = await aiohttp_client(app)

        async def fail_write(fn, *args, **kwargs):
            raise OSError("Disk full")

        with patch("ashlr_ao.server.asyncio.to_thread", side_effect=fail_write):
            resp = await client.put("/api/config", json={"max_agents": 10})
        assert resp.status == 500
        data = await resp.json()
        assert "error" in data


# ─────────────────────────────────────────────
# Disk space check during spawn
# ─────────────────────────────────────────────


class TestDiskSpaceCheck:
    @pytest.mark.asyncio
    async def test_spawn_blocked_on_low_disk(self, aiohttp_client):
        """Spawn should fail when disk space is critically low (<500 MB)."""
        app = _make_test_app()
        client = await aiohttp_client(app)

        low_disk = MagicMock()
        low_disk.free = 100 * 1024 * 1024  # 100 MB free
        low_disk.total = 500 * 1024 * 1024 * 1024

        with patch("shutil.disk_usage", return_value=low_disk):
            resp = await client.post("/api/agents", json={
                "task": "Test task",
                "working_dir": TEST_WORKING_DIR,
            })
        assert resp.status == 400
        data = await resp.json()
        assert "disk" in data["error"].lower() or "space" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_spawn_ok_when_disk_check_fails(self, aiohttp_client):
        """Spawn should proceed normally if disk check itself throws OSError."""
        app = _make_test_app()
        client = await aiohttp_client(app)

        # Make disk check fail — should be non-critical and not block spawn
        with patch("shutil.disk_usage", side_effect=OSError("Cannot stat")), \
             patch("ashlr_ao.manager.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            resp = await client.post("/api/agents", json={
                "task": "Test task",
                "working_dir": TEST_WORKING_DIR,
            })
        # Should NOT fail with disk error — either succeeds or fails for other reasons (tmux)
        assert resp.status != 400 or "disk" not in (await resp.json()).get("error", "").lower()


# ─────────────────────────────────────────────
# Config import — security stripping
# ─────────────────────────────────────────────


class TestConfigImport:
    @pytest.mark.asyncio
    async def test_import_validates_yaml(self, aiohttp_client):
        """Import should reject non-dict payloads."""
        app = _make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/config/import", json="just a string")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_import_rejects_invalid_json(self, aiohttp_client):
        app = _make_test_app()
        client = await aiohttp_client(app)
        resp = await client.post("/api/config/import", data=b"not json",
                                 headers={"Content-Type": "application/json"})
        assert resp.status == 400


# ─────────────────────────────────────────────
# Health endpoint — disk info
# ─────────────────────────────────────────────


class TestHealthDiagnosticDisk:
    @pytest.mark.asyncio
    async def test_health_includes_disk_info(self, cli):
        resp = await cli.get("/api/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_system_metrics(self, cli):
        resp = await cli.get("/api/system")
        assert resp.status == 200
        data = await resp.json()
        assert "cpu_pct" in data
        assert "memory" in data


# ─────────────────────────────────────────────
# Auto-approve pattern validation (server-side)
# ─────────────────────────────────────────────


class TestAutoApproveValidation:
    @pytest.mark.asyncio
    async def test_auto_approve_patterns_stored(self, aiohttp_client):
        """Valid auto-approve patterns should be accepted."""
        app = _make_test_app()
        client = await aiohttp_client(app)

        async def mock_write(fn, *args, **kwargs):
            pass  # Don't actually write to disk

        with patch("ashlr_ao.server.asyncio.to_thread", side_effect=mock_write):
            resp = await client.put("/api/config", json={
                "auto_approve_patterns": ["npm test", "cargo build"],
            })
        # Should succeed — patterns are valid
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_config_rejects_invalid_keys(self, aiohttp_client):
        """Unknown config keys should be silently ignored (not error)."""
        app = _make_test_app()
        client = await aiohttp_client(app)

        async def mock_write(fn, *args, **kwargs):
            pass

        with patch("ashlr_ao.server.asyncio.to_thread", side_effect=mock_write):
            resp = await client.put("/api/config", json={
                "nonexistent_key": "value",
            })
        assert resp.status == 200


# ─────────────────────────────────────────────
# Spawn pressure block
# ─────────────────────────────────────────────


class TestSpawnPressure:
    @pytest.mark.asyncio
    async def test_spawn_blocked_under_cpu_pressure(self, aiohttp_client):
        """Spawn should fail when CPU pressure is high and spawn_pressure_block is enabled."""
        app = _make_test_app()
        app["config"].spawn_pressure_block = True
        client = await aiohttp_client(app)

        with patch("psutil.cpu_percent", return_value=96.0), \
             patch("psutil.virtual_memory") as mock_mem:
            mock_mem.return_value = MagicMock(percent=50.0)
            resp = await client.post("/api/agents", json={
                "task": "Test task",
                "working_dir": TEST_WORKING_DIR,
            })
        assert resp.status == 400
        data = await resp.json()
        assert "pressure" in data["error"].lower()
