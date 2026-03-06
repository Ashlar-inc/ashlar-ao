"""
Ashlr AO — PTY Terminal Management

Manages pseudo-terminal (PTY) sessions for interactive terminal access.
Supports both agent terminal attach (via tmux) and standalone shell terminals.
Communicates with the dashboard via binary WebSocket frames.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
from typing import TYPE_CHECKING

from aiohttp import web, WSMsgType

if TYPE_CHECKING:
    from ashlr_ao.manager import AgentManager

log = logging.getLogger("ashlr")

# Maximum number of standalone (non-agent) terminal sessions
MAX_STANDALONE_TERMINALS = 10


class PTYSession:
    """A single PTY session wrapping a subprocess."""

    __slots__ = (
        "id", "master_fd", "pid", "cols", "rows",
        "_closed", "_reader_handle",
    )

    def __init__(self, session_id: str, master_fd: int, pid: int, cols: int = 200, rows: int = 50):
        self.id = session_id
        self.master_fd = master_fd
        self.pid = pid
        self.cols = cols
        self.rows = rows
        self._closed = False
        self._reader_handle: asyncio.Handle | None = None

    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY window."""
        if self._closed:
            return
        self.cols = cols
        self.rows = rows
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as e:
            log.debug(f"PTY resize failed for {self.id}: {e}")

    def write(self, data: bytes) -> None:
        """Write user input to the PTY."""
        if self._closed:
            return
        try:
            os.write(self.master_fd, data)
        except OSError as e:
            log.debug(f"PTY write failed for {self.id}: {e}")

    def close(self) -> None:
        """Close the PTY session and kill the child process."""
        if self._closed:
            return
        self._closed = True

        if self._reader_handle:
            self._reader_handle.cancel()
            self._reader_handle = None

        # Remove fd from event loop
        try:
            loop = asyncio.get_event_loop()
            loop.remove_reader(self.master_fd)
        except (ValueError, RuntimeError):
            pass

        # Close master fd
        try:
            os.close(self.master_fd)
        except OSError:
            pass

        # Kill child process
        try:
            os.kill(self.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

        # Reap zombie
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass


class PTYManager:
    """Manages PTY sessions and WebSocket connections."""

    def __init__(self):
        self.sessions: dict[str, PTYSession] = {}
        # Map session_id -> set of connected WebSocket clients
        self._clients: dict[str, set[web.WebSocketResponse]] = {}

    def _generate_id(self) -> str:
        return os.urandom(4).hex()

    def open_shell(self, cwd: str = "", cols: int = 200, rows: int = 50) -> PTYSession:
        """Open a new standalone shell PTY session."""
        if len(self._standalone_ids()) >= MAX_STANDALONE_TERMINALS:
            raise ValueError(f"Maximum standalone terminals ({MAX_STANDALONE_TERMINALS}) reached")

        cwd = cwd or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            raise ValueError(f"Directory not found: {cwd}")

        # Validate cwd is under home or /tmp (same security as agent spawn)
        home = os.path.expanduser("~")
        real_cwd = os.path.realpath(cwd)
        real_tmp = os.path.realpath("/tmp")
        if not any(real_cwd == p or real_cwd.startswith(p + os.sep) for p in [home, "/tmp", real_tmp]):
            raise ValueError("Working directory must be under home or /tmp")

        session_id = self._generate_id()
        shell = os.environ.get("SHELL", "/bin/bash")

        master_fd, slave_fd = pty.openpty()

        # Set initial window size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        pid = os.fork()
        if pid == 0:
            # Child process
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(master_fd)
            os.close(slave_fd)
            os.chdir(cwd)
            os.execvp(shell, [shell, "-l"])
            # Never reached
        else:
            # Parent process
            os.close(slave_fd)
            # Set master fd to non-blocking
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            session = PTYSession(session_id, master_fd, pid, cols, rows)
            self.sessions[session_id] = session
            log.info(f"Opened shell PTY {session_id} (pid={pid}, cwd={cwd})")
            return session

    def open_tmux_attach(self, tmux_session: str, cols: int = 200, rows: int = 50) -> PTYSession:
        """Open a PTY that runs `tmux attach-session -t <name>`."""
        session_id = self._generate_id()

        master_fd, slave_fd = pty.openpty()

        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        pid = os.fork()
        if pid == 0:
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(master_fd)
            os.close(slave_fd)
            os.execvp("tmux", ["tmux", "attach-session", "-t", tmux_session])
        else:
            os.close(slave_fd)
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            session = PTYSession(session_id, master_fd, pid, cols, rows)
            self.sessions[session_id] = session
            log.info(f"Opened tmux-attach PTY {session_id} for session {tmux_session}")
            return session

    def close_session(self, session_id: str) -> bool:
        """Close a PTY session."""
        session = self.sessions.pop(session_id, None)
        if not session:
            return False
        session.close()
        self._clients.pop(session_id, None)
        log.info(f"Closed PTY session {session_id}")
        return True

    def close_all(self) -> None:
        """Close all PTY sessions. Called during server shutdown."""
        for sid in list(self.sessions):
            self.close_session(sid)

    def _standalone_ids(self) -> list[str]:
        """Return IDs of standalone (non-agent-attached) sessions."""
        return list(self.sessions.keys())

    async def handle_terminal_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket handler for agent terminal attach.

        GET /ws/terminal/{agent_id}
        Attaches to the agent's tmux session via PTY.
        """
        agent_id = request.match_info["agent_id"]
        manager: AgentManager = request.app["agent_manager"]
        agent = manager.agents.get(agent_id)

        if not agent:
            return web.Response(status=404, text="Agent not found")

        if agent.output_mode == "stream-json":
            return web.Response(status=400, text="Cannot attach terminal to stream-json agent")

        if not agent.tmux_session:
            return web.Response(status=400, text="Agent has no tmux session")

        ws = web.WebSocketResponse(max_msg_size=1 * 1024 * 1024)
        await ws.prepare(request)

        # Parse initial cols/rows from query params
        cols = min(int(request.query.get("cols", 200)), 500)
        rows = min(int(request.query.get("rows", 50)), 200)

        try:
            pty_session = self.open_tmux_attach(agent.tmux_session, cols, rows)
        except Exception as e:
            log.error(f"Failed to open tmux attach PTY for agent {agent_id}: {e}")
            await ws.close(code=1011, message=str(e).encode()[:125])
            return ws

        await self._run_terminal_ws(ws, pty_session)
        return ws

    async def handle_shell_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket handler for standalone shell terminal.

        GET /ws/terminal/new?cwd=/path&cols=200&rows=50
        """
        cwd = request.query.get("cwd", "")
        cols = min(int(request.query.get("cols", 200)), 500)
        rows = min(int(request.query.get("rows", 50)), 200)

        ws = web.WebSocketResponse(max_msg_size=1 * 1024 * 1024)
        await ws.prepare(request)

        try:
            pty_session = self.open_shell(cwd, cols, rows)
        except ValueError as e:
            await ws.close(code=1008, message=str(e).encode()[:125])
            return ws
        except Exception as e:
            log.error(f"Failed to open shell PTY: {e}")
            await ws.close(code=1011, message=str(e).encode()[:125])
            return ws

        await self._run_terminal_ws(ws, pty_session)
        return ws

    async def _run_terminal_ws(self, ws: web.WebSocketResponse, session: PTYSession) -> None:
        """Main loop: bridge PTY I/O with WebSocket using binary frames."""
        loop = asyncio.get_event_loop()
        output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

        # Register reader callback for PTY master fd → output_queue
        def on_pty_readable():
            try:
                data = os.read(session.master_fd, 65536)
                if data:
                    try:
                        output_queue.put_nowait(data)
                    except asyncio.QueueFull:
                        # Drop oldest on backpressure
                        try:
                            output_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        output_queue.put_nowait(data)
            except OSError:
                # PTY closed — signal shutdown
                try:
                    output_queue.put_nowait(b"")
                except asyncio.QueueFull:
                    pass

        loop.add_reader(session.master_fd, on_pty_readable)

        # Track client
        clients = self._clients.setdefault(session.id, set())
        clients.add(ws)

        # Send session info as first text message
        import json
        await ws.send_str(json.dumps({
            "type": "pty_session",
            "session_id": session.id,
            "cols": session.cols,
            "rows": session.rows,
        }))

        # Writer task: output_queue → WebSocket
        async def writer():
            while True:
                data = await output_queue.get()
                if not data:  # Empty bytes = PTY closed
                    break
                try:
                    await ws.send_bytes(data)
                except (ConnectionError, RuntimeError):
                    break

        writer_task = asyncio.create_task(writer())

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    # User input → PTY
                    session.write(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    # Control messages (resize, etc.)
                    try:
                        import json
                        ctrl = json.loads(msg.data)
                        if ctrl.get("type") == "resize":
                            c = min(int(ctrl.get("cols", session.cols)), 500)
                            r = min(int(ctrl.get("rows", session.rows)), 200)
                            session.resize(c, r)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except Exception as e:
            log.debug(f"Terminal WS error: {e}")
        finally:
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass
            clients.discard(ws)
            # Only close the PTY if no other clients are connected
            if not clients:
                self.close_session(session.id)

        return
