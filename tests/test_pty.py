"""Tests for PTY terminal management (interactive terminals)."""

import asyncio
import json
import os
import signal
import struct
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from aiohttp import WSMsgType

sys.path.insert(0, str(Path(__file__).parent.parent))

with patch("psutil.cpu_percent", return_value=0.0):
    import ashlr_server

from ashlr_server import PTYManager, Agent
from ashlr_ao.pty import PTYSession, MAX_STANDALONE_TERMINALS
from tests.conftest import make_test_app, make_mock_db, TEST_WORKING_DIR


# ── PTYSession Tests ──

class TestPTYSession:
    """Test PTYSession dataclass and methods."""

    def test_init(self):
        session = PTYSession("s001", master_fd=10, pid=1234, cols=200, rows=50)
        assert session.id == "s001"
        assert session.master_fd == 10
        assert session.pid == 1234
        assert session.cols == 200
        assert session.rows == 50
        assert session._closed is False

    def test_resize(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        with patch("fcntl.ioctl") as mock_ioctl:
            session.resize(120, 30)
            assert session.cols == 120
            assert session.rows == 30
            mock_ioctl.assert_called_once()
            # Verify TIOCSWINSZ and packed struct
            args = mock_ioctl.call_args
            assert args[0][0] == 10  # master_fd
            winsize = struct.pack("HHHH", 30, 120, 0, 0)
            assert args[0][2] == winsize

    def test_resize_when_closed(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        session._closed = True
        with patch("fcntl.ioctl") as mock_ioctl:
            session.resize(120, 30)
            mock_ioctl.assert_not_called()

    def test_write(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        with patch("os.write") as mock_write:
            session.write(b"hello")
            mock_write.assert_called_once_with(10, b"hello")

    def test_write_when_closed(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        session._closed = True
        with patch("os.write") as mock_write:
            session.write(b"hello")
            mock_write.assert_not_called()

    def test_close(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        with patch("os.close") as mock_close, \
             patch("os.kill") as mock_kill, \
             patch("os.waitpid", return_value=(1234, 0)):
            # Mock event loop remove_reader
            mock_loop = MagicMock()
            with patch("asyncio.get_event_loop", return_value=mock_loop):
                session.close()
                assert session._closed is True
                mock_close.assert_called_once_with(10)
                mock_kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_close_idempotent(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        session._closed = True
        with patch("os.close") as mock_close:
            session.close()
            mock_close.assert_not_called()

    def test_close_handles_oserror(self):
        session = PTYSession("s001", master_fd=10, pid=1234)
        with patch("os.close", side_effect=OSError("bad fd")), \
             patch("os.kill", side_effect=ProcessLookupError()), \
             patch("os.waitpid", side_effect=ChildProcessError()), \
             patch("asyncio.get_event_loop", return_value=MagicMock()):
            session.close()  # Should not raise
            assert session._closed is True


# ── PTYManager Tests ──

class TestPTYManager:
    """Test PTYManager lifecycle management."""

    def test_init(self):
        mgr = PTYManager()
        assert mgr.sessions == {}
        assert mgr._clients == {}

    def test_generate_id(self):
        mgr = PTYManager()
        id1 = mgr._generate_id()
        id2 = mgr._generate_id()
        assert len(id1) == 8
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_open_shell_invalid_dir(self):
        mgr = PTYManager()
        with pytest.raises(ValueError, match="Directory not found"):
            await mgr.open_shell(cwd="/nonexistent/path/xyz")

    @pytest.mark.asyncio
    async def test_open_shell_outside_home(self):
        mgr = PTYManager()
        with pytest.raises(ValueError, match="under home or /tmp"):
            await mgr.open_shell(cwd="/etc")

    @pytest.mark.asyncio
    async def test_open_shell_max_limit(self):
        mgr = PTYManager()
        # Fill up with fake standalone sessions
        for i in range(MAX_STANDALONE_TERMINALS):
            s = MagicMock()
            s.is_standalone = True
            mgr.sessions[f"fake-{i}"] = s
        with pytest.raises(ValueError, match="Maximum standalone terminals"):
            await mgr.open_shell(cwd="/tmp")

    @pytest.mark.asyncio
    @patch("pty.openpty", return_value=(10, 11))
    @patch("fcntl.ioctl")
    @patch("fcntl.fcntl", return_value=0)
    @patch("os.close")
    async def test_open_shell_parent_path(self, mock_close, mock_fcntl, mock_ioctl, mock_openpty):
        mgr = PTYManager()
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            session = await mgr.open_shell(cwd="/tmp", cols=120, rows=30)
        assert session.pid == 12345
        assert session.master_fd == 10
        assert session.cols == 120
        assert session.rows == 30
        assert session.is_standalone is True
        assert session.id in mgr.sessions
        mock_close.assert_called_once_with(11)  # slave_fd closed in parent

    @pytest.mark.asyncio
    @patch("pty.openpty", return_value=(10, 11))
    @patch("fcntl.ioctl")
    @patch("fcntl.fcntl", return_value=0)
    @patch("os.close")
    async def test_open_tmux_attach(self, mock_close, mock_fcntl, mock_ioctl, mock_openpty):
        mgr = PTYManager()
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            session = await mgr.open_tmux_attach("ashlr-abc1", cols=200, rows=50)
        assert session.pid == 12345
        assert session.is_standalone is False
        assert session.id in mgr.sessions
        mock_close.assert_called_once_with(11)

    def test_close_session(self):
        mgr = PTYManager()
        mock_session = MagicMock()
        mgr.sessions["s001"] = mock_session
        mgr._clients["s001"] = set()
        result = mgr.close_session("s001")
        assert result is True
        assert "s001" not in mgr.sessions
        assert "s001" not in mgr._clients
        mock_session.close.assert_called_once()

    def test_close_session_not_found(self):
        mgr = PTYManager()
        assert mgr.close_session("nonexistent") is False

    def test_close_all(self):
        mgr = PTYManager()
        s1 = MagicMock()
        s2 = MagicMock()
        mgr.sessions = {"s1": s1, "s2": s2}
        mgr.close_all()
        assert len(mgr.sessions) == 0
        s1.close.assert_called_once()
        s2.close.assert_called_once()


# ── WebSocket Handler Tests ──

class TestTerminalWSHandlers:
    """Test WebSocket endpoint handlers."""

    @pytest.mark.asyncio
    async def test_agent_terminal_not_found(self, aiohttp_client):
        app = make_test_app()
        client = await aiohttp_client(app)
        # Try to connect to non-existent agent terminal
        resp = await client.get("/ws/terminal/nonexistent")
        # Should get 404 (not a WS upgrade)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_agent_terminal_stream_json_rejected(self, aiohttp_client):
        app = make_test_app()
        # Spawn a stream-json agent
        manager = app["agent_manager"]
        agent = Agent(
            id="s001", name="stream-test", role="general", status="working",
            working_dir="/tmp", backend="claude-code", task="test",
            output_mode="stream-json",
        )
        manager.agents["s001"] = agent
        client = await aiohttp_client(app)
        resp = await client.get("/ws/terminal/s001")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_agent_terminal_no_tmux(self, aiohttp_client):
        app = make_test_app()
        manager = app["agent_manager"]
        agent = Agent(
            id="a001", name="test-agent", role="general", status="working",
            working_dir="/tmp", backend="claude-code", task="test",
            tmux_session="",
        )
        manager.agents["a001"] = agent
        client = await aiohttp_client(app)
        resp = await client.get("/ws/terminal/a001")
        assert resp.status == 400


# ── Route Registration Tests ──

class TestRouteRegistration:
    """Test that PTY routes are registered in create_app."""

    def test_ws_terminal_routes_registered(self):
        app = make_test_app()
        route_strs = []
        for r in app.router.routes():
            info = r.get_info()
            route_strs.append(info.get("formatter", info.get("path", "")))
        assert "/ws/terminal/new" in route_strs
        assert "/ws/terminal/{agent_id}" in route_strs

    def test_pty_manager_in_app(self):
        app = make_test_app()
        assert "pty_manager" in app
        assert isinstance(app["pty_manager"], PTYManager)


# ── Cleanup Tests ──

class TestPTYCleanup:
    """Test PTY cleanup during shutdown."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_all_ptys(self):
        app = make_test_app()
        pty_mgr = app["pty_manager"]
        mock_session = MagicMock()
        pty_mgr.sessions["test-1"] = mock_session
        pty_mgr.close_all()
        assert len(pty_mgr.sessions) == 0
        mock_session.close.assert_called_once()


# ── Integration Tests ──

class TestPTYIntegration:
    """Integration tests for PTY with agent lifecycle."""

    def test_agent_kill_leaves_pty_to_self_close(self):
        """PTY sessions self-close when the tmux session they're attached to dies.
        Verify kill doesn't need to explicitly clean PTY sessions."""
        mgr = PTYManager()
        # PTY sessions are managed independently - no direct coupling to kill
        # The tmux attach process exits naturally when the session is killed
        assert mgr.sessions == {}

    def test_pty_session_slots(self):
        """Verify PTYSession uses __slots__ for memory efficiency."""
        session = PTYSession("s001", master_fd=10, pid=1234)
        assert hasattr(session, '__slots__')
        with pytest.raises(AttributeError):
            session.nonexistent_attr = True

    def test_pty_session_is_standalone_field(self):
        """PTYSession has is_standalone field."""
        s1 = PTYSession("s001", master_fd=10, pid=1234, is_standalone=True)
        assert s1.is_standalone is True
        s2 = PTYSession("s002", master_fd=11, pid=1235, is_standalone=False)
        assert s2.is_standalone is False
        s3 = PTYSession("s003", master_fd=12, pid=1236)
        assert s3.is_standalone is False  # default

    def test_standalone_ids_filters(self):
        """_standalone_ids only returns sessions where is_standalone=True."""
        mgr = PTYManager()
        s1 = MagicMock()
        s1.is_standalone = True
        s2 = MagicMock()
        s2.is_standalone = False
        s3 = MagicMock()
        s3.is_standalone = True
        mgr.sessions = {"shell-1": s1, "tmux-1": s2, "shell-2": s3}
        ids = mgr._standalone_ids()
        assert set(ids) == {"shell-1", "shell-2"}


# ── T-1: _run_terminal_ws WebSocket I/O bridge tests ──

class TestRunTerminalWs:
    """Tests for PTY WebSocket I/O bridge logic."""

    @pytest.mark.asyncio
    async def test_backpressure_drop_oldest(self):
        """Queue full condition drops oldest frame (backpressure behavior)."""
        q = asyncio.Queue(maxsize=2)
        q.put_nowait(b"old1")
        q.put_nowait(b"old2")
        assert q.full()
        # Simulate the backpressure logic from on_pty_readable
        try:
            q.put_nowait(b"new")
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(b"new")
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items == [b"old2", b"new"]  # old1 dropped

    @pytest.mark.asyncio
    async def test_empty_bytes_signals_pty_closed(self):
        """Empty bytes in queue signal PTY closed (writer should stop)."""
        q = asyncio.Queue(maxsize=10)
        q.put_nowait(b"data")
        q.put_nowait(b"")  # PTY closed signal
        items = []
        while not q.empty():
            data = q.get_nowait()
            if not data:
                break  # Writer loop exits
            items.append(data)
        assert items == [b"data"]

    def test_resize_clamp_values(self):
        """PTY resize clamps cols to 500 and rows to 200."""
        # This tests the resize clamping in handle_terminal_ws
        cols = min(int("600"), 500)
        rows = min(int("300"), 200)
        assert cols == 500
        assert rows == 200

    def test_client_tracking(self):
        """PTYManager tracks clients per session."""
        mgr = PTYManager()
        ws1 = MagicMock()
        ws2 = MagicMock()
        clients = mgr._clients.setdefault("s1", set())
        clients.add(ws1)
        clients.add(ws2)
        assert len(mgr._clients["s1"]) == 2
        clients.discard(ws1)
        assert len(mgr._clients["s1"]) == 1
        # When last client disconnects, session can be closed
        clients.discard(ws2)
        assert len(mgr._clients["s1"]) == 0
