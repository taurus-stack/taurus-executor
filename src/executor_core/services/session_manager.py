import asyncio
import fcntl
import logging
import os
import re
import time
import uuid
from typing import Optional, Dict, AsyncGenerator

from ..executors.command_executor import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)

# ANSI escape sequence regex
ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
# Shell prompt pattern (matches bash/zsh "$" or "#" prompts)
PROMPT_RE = re.compile(r'[$#]\s*$')
# Password prompts in multiple languages
# Chinese terms: 密码 (password), 口令 (passphrase) - for matching Chinese system prompts
PASSWORD_PROMPT_RE = re.compile(r'([Pp]assword|[Pp]assphrase|密码|口令)[:：]?\s*$', re.IGNORECASE)
# Authentication failure messages
# Chinese terms: 认证失败 (authentication failed), 鉴定故障 (authentication error) - for matching Chinese system messages
AUTH_FAILURE_RE = re.compile(r'(Authentication\s+failure|incorrect\s+password|认证失败|鉴定故障|su:\s)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# PTY + posix_spawn based interactive shell (no fork, no pexpect)
# ---------------------------------------------------------------------------

def _spawn_pty_process(
    argv: list,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
) -> tuple[int, int]:
    """Create a child process attached to a pseudo-terminal using posix_spawn.

    Returns (master_fd, child_pid). The caller must close master_fd when done.
    """
    # 1. Create pseudo-terminal pair (master, slave) -- pure syscall, no fork
    master_fd, slave_fd = os.openpty()

    # 2. Set raw mode on the slave side so the shell behaves like a terminal
    try:
        import termios
        attrs = termios.tcgetattr(slave_fd)
        attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
    except Exception:
        pass  # Best effort

    # 3. Resolve executable path
    env_dict = {**os.environ, **(env or {})} if env else os.environ.copy()

    def _resolve_in_path(exe: str, env: dict) -> str:
        if os.path.isabs(exe):
            return exe
        if "/" in exe:
            return os.path.abspath(exe)
        path_env = env.get("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
        for directory in path_env.split(os.pathsep):
            if not directory:
                continue
            candidate = os.path.join(directory, exe)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        raise FileNotFoundError(f"Command not found in PATH: {exe}")

    resolved_exe = _resolve_in_path(argv[0], env_dict)
    exec_argv = [resolved_exe, *argv[1:]]

    # 4. File actions: dup slave_fd onto 0, 1, 2 in the child; close everything else
    file_actions = [
        (os.POSIX_SPAWN_DUP2, slave_fd, 0),
        (os.POSIX_SPAWN_DUP2, slave_fd, 1),
        (os.POSIX_SPAWN_DUP2, slave_fd, 2),
        (os.POSIX_SPAWN_CLOSE, slave_fd),
        (os.POSIX_SPAWN_CLOSE, master_fd),
    ]

    # 5. posix_spawn -- does NOT call fork(), uses clone() + execve() directly
    def _do_spawn() -> int:
        if cwd and os.path.isdir(cwd):
            old_cwd = os.getcwd()
            os.chdir(cwd)
            try:
                return os.posix_spawn(
                    exec_argv[0],
                    exec_argv,
                    env_dict,
                    file_actions=file_actions,
                    setpgroup=0,
                )
            finally:
                os.chdir(old_cwd)
        return os.posix_spawn(
            exec_argv[0],
            exec_argv,
            env_dict,
            file_actions=file_actions,
            setpgroup=0,
        )

    try:
        child_pid = _do_spawn()
    except Exception:
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.close(slave_fd)
        except OSError:
            pass
        raise

    # In parent, we only need master_fd; slave_fd belongs to the child now
    try:
        os.close(slave_fd)
    except OSError:
        pass

    # Set non-blocking on master so our async readers work well
    try:
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except Exception:
        pass

    return master_fd, child_pid


class _PTYShell:
    """A shell process attached to a PTY, managed via posix_spawn (no fork).

    Implements a minimal read/write/expect interface that replaces pexpect.
    """

    def __init__(self, master_fd: int, pid: int):
        self.master_fd = master_fd
        self.pid = pid
        self._buffer = ""
        self._closed = False

    def fileno(self) -> int:
        return self.master_fd

    def isalive(self) -> bool:
        if self._closed:
            return False
        try:
            rpid, _ = os.waitpid(self.pid, os.WNOHANG)
            return rpid == 0
        except ChildProcessError:
            return False
        except OSError:
            return False

    def send(self, data: str) -> int:
        """Write string data to the PTY master (thread-safe, no fork)."""
        if self._closed:
            return 0
        try:
            raw = data.encode("utf-8")
            return os.write(self.master_fd, raw)
        except (OSError, ValueError):
            return 0

    def sendline(self, line: str) -> int:
        return self.send(line + "\n")

    def sendcontrol(self, char: str) -> int:
        """Send a control character (e.g., 'c' for Ctrl-C)."""
        if len(char) == 0:
            return 0
        ctrl_byte = ord(char.lower()) - ord('a') + 1
        if char.lower() == 'c':
            ctrl_byte = 3  # SIGINT
        elif char.lower() == 'd':
            ctrl_byte = 4  # EOF
        try:
            return os.write(self.master_fd, bytes([ctrl_byte]))
        except (OSError, ValueError):
            return 0

    def _read_some(self, timeout_s: float) -> str:
        """Read up to 4096 bytes from the PTY with a timeout."""
        deadline = time.monotonic() + timeout_s
        data_buf = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = os.read(self.master_fd, 4096)
                if chunk:
                    data_buf.extend(chunk)
                    # Keep reading if more data is available (don't spin too long)
                    if len(data_buf) < 8192:
                        continue
                break
            except BlockingIOError:
                # No data available right now -- sleep briefly
                sleep_time = min(remaining, 0.05)
                time.sleep(sleep_time)
                continue
            except OSError:
                break
        try:
            return data_buf.decode("utf-8", errors="replace")
        except Exception:
            return ""

    def expect(
        self,
        patterns: list,
        timeout: float = 10,
    ) -> int:
        """Blocking expect: read from PTY until one of the patterns matches or timeout.

        Patterns can be compiled regexes or strings (treated as plain text).
        Returns the index of the matched pattern, or -1 for EOF, -2 for timeout.
        Caller can access self._buffer (everything read) afterwards.
        """
        compiled = []
        for p in patterns:
            if hasattr(p, 'search'):
                compiled.append(p)
            else:
                compiled.append(re.compile(p))

        deadline = time.monotonic() + timeout
        self._buffer = ""

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            chunk = self._read_some(min(max(remaining, 0.05), 0.5))
            if chunk:
                self._buffer += chunk
                for i, pat in enumerate(compiled):
                    if pat.search(self._buffer):
                        return i
            else:
                # Check if process is still alive
                if not self.isalive():
                    return -1  # EOF
                # Sleep to avoid busy waiting
                time.sleep(0.05)

        return -2  # TIMEOUT

    def read_nonblocking(self, size: int, timeout: float = 2.0) -> str:
        """Read up to size bytes from the PTY, with a timeout."""
        return self._read_some(timeout)[:size]

    @property
    def before(self) -> str:
        """Return everything read by the last expect() call."""
        return self._buffer

    def close(self, force: bool = False) -> None:
        """Close the PTY and optionally force-kill the child process."""
        if self._closed:
            return
        self._closed = True
        try:
            if force:
                try:
                    pgid = os.getpgid(self.pid)
                    os.killpg(pgid, 9)
                except (OSError, ProcessLookupError):
                    os.kill(self.pid, 9)
                    pass
            try:
                os.waitpid(self.pid, 0)
            except (ChildProcessError, OSError):
                pass
        except Exception:
            pass
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass


class InteractiveSession:
    """Interactive shell session with state persistence. Based on PTY + posix_spawn to avoid gRPC fork issues."""

    def __init__(
        self,
        session_id: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        working_directory: Optional[str] = None,
        environment: Optional[dict] = None,
        shell: str = "/bin/bash",
        timeout: int = 3600,
    ):
        self.session_id = session_id
        if username:
            self.username = username
        else:
            try:
                self.username = os.getlogin()
            except OSError:
                self.username = os.getenv('USER') or os.getenv('USERNAME') or 'unknown'
        self.password = password
        self.working_directory = working_directory or os.getcwd()
        self.environment = environment or {}
        self.shell = shell
        self.timeout = timeout

        self.child: Optional[_PTYShell] = None
        self.created_at = time.time()
        self.last_active = time.time()
        self.is_alive = False
        self._lock = asyncio.Lock()

    async def create(self) -> bool:
        """Create interactive shell session."""
        try:
            if self.password:
                # Use su to switch to target user
                await self._create_with_su()
            else:
                # Create shell with current user
                await self._create_local_shell()

            self.is_alive = True
            logger.info(f"Session {self.session_id} created for user {self.username}")
            return True

        except Exception as e:
            logger.error(f"Failed to create session {self.session_id}: {e}")
            await self.close()
            return False

    async def _create_with_su(self):
        """Create session with su + password (using PTY + posix_spawn, no fork)."""
        loop = asyncio.get_event_loop()

        # 1. Create su process with PTY (posix_spawn-based, no fork)
        master_fd, pid = _spawn_pty_process(
            ["su", "-", self.username],
            env=self.environment or None,
        )
        self.child = _PTYShell(master_fd, pid)

        # 2. Wait for password prompt
        idx = await loop.run_in_executor(
            None,
            lambda: self.child.expect(
                [PASSWORD_PROMPT_RE],
                timeout=10,
            )
        )

        if idx != 0:
            output = getattr(self.child, '_buffer', '')
            raise PermissionError(f"su did not respond to password prompt, output: {output}")

        # 3. Send password
        self.child.sendline(self.password)

        # 4. Check authentication result (wait for shell prompt or auth failure)
        idx = await loop.run_in_executor(
            None,
            lambda: self.child.expect(
                [PROMPT_RE, AUTH_FAILURE_RE],
                timeout=10,
            )
        )

        if idx == 0:
            # Authentication succeeded
            pass
        elif idx == 1:
            error_msg = getattr(self.child, '_buffer', 'Unknown error')
            raise PermissionError(f"User {self.username} authentication failed: {error_msg}")
        else:
            output = getattr(self.child, '_buffer', '')
            raise PermissionError(f"su authentication process abnormal, output: {output}")

        # Clear password from memory
        self.password = None

        # Change to working directory
        if self.working_directory:
            self.child.sendline(f'cd {self.working_directory}')
            await loop.run_in_executor(
                None,
                lambda: self.child.expect([PROMPT_RE], timeout=5)
            )

    async def _create_local_shell(self):
        """Create local shell with current user (using PTY + posix_spawn, no fork)."""
        loop = asyncio.get_event_loop()

        env = os.environ.copy()
        env.update(self.environment)

        master_fd, pid = _spawn_pty_process(
            [self.shell, "-i", "-l"],
            env=env,
            cwd=self.working_directory,
        )
        self.child = _PTYShell(master_fd, pid)

        # Wait for shell initialization (prompt)
        idx = await loop.run_in_executor(
            None,
            lambda: self.child.expect([PROMPT_RE], timeout=10)
        )
        if idx != 0:
            raise RuntimeError("Shell initialization failed, prompt not detected")

    async def execute(self, command: str, timeout: int = 30) -> AsyncGenerator[StreamEvent, None]:
        """Execute command in session, streaming output."""
        async with self._lock:
            if not self.is_alive or not self.child:
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    data=b"Session is not active"
                )
                return

            self.last_active = time.time()

            try:
                # Send command to the shell
                self.child.sendline(command)

                # Stream output until prompt re-appears
                async for event in self._read_output(timeout):
                    yield event
                    self.last_active = time.time()

            except Exception as e:
                logger.error(f"Execution error in session {self.session_id}: {e}")
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    data=f"Execution error: {str(e)}".encode()
                )

    async def _read_output(self, timeout: int) -> AsyncGenerator[StreamEvent, None]:
        """Read command output until prompt reappears."""
        loop = asyncio.get_event_loop()
        buffer = ""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                idx = await loop.run_in_executor(
                    None,
                    lambda: self.child.expect([PROMPT_RE], timeout=0.5)
                )

                if idx == 0:
                    # Got a prompt match -- command finished
                    if self.child._buffer:
                        buffer += self.child._buffer
                    # Strip the echoed command line and prompt
                    lines = buffer.splitlines()
                    # Filter out the command echo at the top if present
                    if lines:
                        cleaned_output = "\n".join(lines[1:-1]) if len(lines) > 2 else ""
                    else:
                        cleaned_output = ""
                    clean_buffer = ANSI_ESCAPE_RE.sub('', cleaned_output)
                    if clean_buffer.strip():
                        yield StreamEvent(
                            type=StreamEventType.STDOUT,
                            data=(clean_buffer + "\n").encode('utf-8')
                        )
                    yield StreamEvent(
                        type=StreamEventType.EXIT,
                        data=b"",
                        exit_code=0
                    )
                    return
                elif idx < 0:
                    # EOF -- process exited
                    if buffer:
                        clean_buffer = ANSI_ESCAPE_RE.sub('', buffer)
                        if clean_buffer.strip():
                            yield StreamEvent(
                                type=StreamEventType.STDOUT,
                                data=clean_buffer.encode('utf-8')
                            )
                    self.is_alive = False
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        data=b"Session ended unexpectedly"
                    )
                    return
                else:
                    # Timeout on individual expect -- stream accumulated output
                    if self.child._buffer:
                        buffer += self.child._buffer
                    if buffer.strip():
                        clean_buffer = ANSI_ESCAPE_RE.sub('', buffer)
                        if clean_buffer.strip():
                            yield StreamEvent(
                                type=StreamEventType.STDOUT,
                                data=clean_buffer.encode('utf-8')
                            )
                        buffer = ""

            except Exception as e:
                logger.error(f"Error reading PTY output: {e}")
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    data=f"Execution error: {str(e)}".encode()
                )
                return

        # Overall timeout reached
        try:
            self.child.sendcontrol('c')
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.child.expect([PROMPT_RE], timeout=2)
            )
        except Exception:
            pass

        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=f"Command timed out after {timeout}s, terminated".encode()
        )

    async def close(self):
        """Close session."""
        try:
            if self.child and self.child.isalive():
                self.child.sendline('exit')
                try:
                    # Give it a moment to exit cleanly
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: time.sleep(0.5)
                    )
                except Exception:
                    pass
                self.child.close(force=True)
            elif self.child:
                self.child.close(force=True)
        except Exception as e:
            logger.warning(f"Error closing session {self.session_id}: {e}")
            try:
                if self.child:
                    self.child.close(force=True)
            except Exception:
                pass
        finally:
            self.is_alive = False
            logger.info(f"Session {self.session_id} closed")


class SessionManager:
    """Session manager, responsible for creating, tracking, and cleaning sessions."""

    def __init__(self, max_sessions: int = 10, session_timeout: int = 3600):
        self.sessions: Dict[str, InteractiveSession] = {}
        self.max_sessions = max_sessions
        self.session_timeout = session_timeout
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start session manager."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"Session manager started (max_sessions={self.max_sessions})")

    async def stop(self):
        """Stop session manager."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all sessions
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)

        logger.info("Session manager stopped")

    async def _cleanup_loop(self):
        """Background loop to clean up dead/expired sessions."""
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                for session_id, session in list(self.sessions.items()):
                    if not session.is_alive or (now - session.last_active > self.session_timeout):
                        await self.close_session(session_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Session cleanup loop error: {e}")

    async def create_session(self, **kwargs) -> Optional[str]:
        """Create new session."""
        if len(self.sessions) >= self.max_sessions:
            logger.error(f"Maximum sessions reached ({self.max_sessions})")
            return None

        session_id = str(uuid.uuid4())
        session = InteractiveSession(session_id=session_id, **kwargs)

        if await session.create():
            self.sessions[session_id] = session
            logger.info(f"Session {session_id} created (total: {len(self.sessions)})")
            return session_id
        else:
            return None

    async def execute_in_session(self, session_id: str, command: str, timeout: int = 30):
        """Execute command in specified session."""
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not session.is_alive:
            raise RuntimeError(f"Session {session_id} is not active")

        async for event in session.execute(command, timeout):
            yield event

    async def close_session(self, session_id: str) -> bool:
        """Close specified session."""
        session = self.sessions.pop(session_id, None)
        if session:
            await session.close()
            logger.info(f"Session {session_id} closed (remaining: {len(self.sessions)})")
            return True
        return False

    def list_sessions(self) -> list:
        """List all active sessions."""
        return [
            {
                "session_id": s.session_id,
                "username": s.username,
                "working_directory": s.working_directory,
                "created_at": s.created_at,
                "last_active": s.last_active,
                "is_alive": s.is_alive,
            }
            for s in self.sessions.values()
        ]