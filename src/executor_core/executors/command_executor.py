#!/usr/bin/env python3
"""
Command executor for running shell/subprocess commands and streaming their output.

This module provides a robust, async-first command executor that:
  * Runs subprocesses with controlled timeouts.
  * Streams stdout/stderr output in real time.
  * Manages process groups for clean termination.
  * Reports the child PID to callers for signal management.
  * Handles shell-mode execution without relying on bash history.

CRITICAL IMPLEMENTATION NOTE
============================
We MUST use ``os.posix_spawn`` instead of ``subprocess.Popen`` and
``asyncio.create_subprocess_exec``.

Rationale
---------
1. ``subprocess.Popen`` and ``asyncio.create_subprocess_exec`` both use
   ``fork()`` internally on Linux.
2. gRPC registers ``pthread_atfork`` handlers that acquire internal locks
   before ``fork()`` is called. If any gRPC thread holds a lock when fork
   occurs, the child process deadlocks or calls ``abort()`` -- this produces
   the infamous ``exit code -6`` (SIGABRT) problem.
3. ``os.posix_spawn`` uses ``clone()`` + ``execve()`` directly and does NOT
   invoke ``pthread_atfork`` handlers. It is therefore safe in any
   multi-threaded process, including gRPC servers.

The implementation wraps ``os.posix_spawn`` in a lightweight
:class:`_SpawnedProcess` adapter whose interface mirrors the subset of
``subprocess.Popen`` actually used by this module (``pid``, ``poll``,
``wait``, ``returncode``, ``stdout``, ``stderr``).
"""

import asyncio
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, Callable, List, Optional

from ..infra.logger import CommandLogger, get_logger

logger = get_logger(__name__)

_CWD_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Stream event types
# ---------------------------------------------------------------------------

class StreamEventType(Enum):
    """Enumeration of the different event types produced while running a command."""

    STDOUT = "stdout"
    STDERR = "stderr"
    EXIT = "exit"
    ERROR = "error"


@dataclass
class StreamEvent:
    """A single event (output chunk / exit / error) produced while executing."""

    type: StreamEventType
    data: bytes = b""
    exit_code: Optional[int] = None


class CommandTimeoutError(Exception):
    """Raised when a command exceeds its allowed execution time."""

    def __init__(self, timeout: float, command: str, args: List[str]):
        super().__init__(
            f"Command '{command} {' '.join(args)}' timed out after {timeout}s"
        )
        self.timeout = timeout
        self.command = command
        self.args = args


class CommandExecutionError(Exception):
    """Raised when a command fails to start or returns a non-zero exit code."""

    def __init__(self, message: str, exit_code: Optional[int] = None):
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# posix_spawn-based subprocess adapter (safer than Popen under gRPC)
# ---------------------------------------------------------------------------

class _SpawnedProcess:
    """Lightweight adapter wrapping ``os.posix_spawn`` for ``subprocess.Popen``-like usage.

    Provides a minimal subset of ``subprocess.Popen``'s interface:
      ``pid``, ``stdout`` (file-like), ``stderr`` (file-like or None),
      ``returncode``, ``poll()``, ``wait()``.

    The adapter is intentionally small -- it only implements what this module
    actually needs.
    """

    __slots__ = ("pid", "stdout", "stderr", "returncode", "_reaped")

    def __init__(self, pid: int, stdout_fd: int, stderr_fd: Optional[int]):
        self.pid = pid
        # Wrap raw file descriptors in file objects so that ``to_thread``
        # callers can perform ``.read()`` on them. We use ``os.fdopen`` for
        # binary, unbuffered I/O.
        self.stdout = os.fdopen(stdout_fd, "rb", 0) if stdout_fd >= 0 else None
        self.stderr = (
            os.fdopen(stderr_fd, "rb", 0) if stderr_fd is not None and stderr_fd >= 0 else None
        )
        self.returncode: Optional[int] = None
        self._reaped = False

    def poll(self) -> Optional[int]:
        """Check if the child process has exited. Returns ``None`` while running."""
        if self._reaped:
            return self.returncode
        try:
            wpid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            # Already reaped by someone else (should not happen in our flow).
            self._reaped = True
            if self.returncode is None:
                self.returncode = 0
            return self.returncode
        except OSError:
            return self.returncode

        if wpid == 0:
            return None  # still running
        self._reaped = True
        if os.WIFEXITED(status):
            self.returncode = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            self.returncode = -os.WTERMSIG(status)
        else:
            self.returncode = -1
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        """Block until the child process exits or ``timeout`` elapses."""
        if self._reaped:
            return self.returncode if self.returncode is not None else 0

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                wpid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self._reaped = True
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode
            except OSError:
                return self.returncode if self.returncode is not None else 0

            if wpid != 0:
                self._reaped = True
                if os.WIFEXITED(status):
                    self.returncode = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    self.returncode = -os.WTERMSIG(status)
                else:
                    self.returncode = -1
                return self.returncode

            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(
                    f"Process {self.pid}", timeout if timeout else 0, None
                )
            time.sleep(0.05)


def _resolve_in_path(executable: str, env_dict: dict) -> Optional[str]:
    """Search for ``executable`` in ``PATH`` like ``subprocess.Popen`` does.

    Returns the absolute path to the executable, or ``None`` if not found.
    Already-absolute paths are returned unchanged.
    """
    if os.path.isabs(executable):
        return executable if os.path.isfile(executable) and os.access(executable, os.X_OK) else None
    if "/" in executable:
        # Relative path with slash -- resolve relative to cwd
        resolved = os.path.abspath(executable)
        return resolved if os.path.isfile(resolved) and os.access(resolved, os.X_OK) else None
    # Plain name -- search PATH
    path_env = env_dict.get("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, executable)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _spawn_process_via_posix_spawn(
    argv: List[str],
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    merge_streams: bool = False,
) -> _SpawnedProcess:
    """Create a child process using ``os.posix_spawn``.

    The child is created in its own process group (``setpgroup=0``) so that
    the caller can terminate the process tree with a single ``killpg`` call.

    Unlike ``os.posix_spawn`` itself, this function searches ``PATH`` for
    the executable so it behaves like ``subprocess.Popen``.
    """

    if not argv:
        raise ValueError("argv must not be empty")

    # Environment
    env_dict = {**os.environ, **(env or {})} if env else os.environ.copy()

    # Resolve argv[0] via PATH (mimic subprocess.Popen behavior)
    resolved_exe = _resolve_in_path(argv[0], env_dict)
    if resolved_exe is None:
        raise FileNotFoundError(f"Command not found: {argv[0]}")
    exec_argv = [resolved_exe, *argv[1:]]

    # Pipes
    stdout_r, stdout_w = os.pipe()
    if merge_streams:
        stderr_r, stderr_w = None, None
    else:
        stderr_r, stderr_w = os.pipe()

    # File actions for the child process:
    #   * dup2 stdout_w onto fd 1
    #   * dup2 stderr_w onto fd 2 (or stdout_w if merging)
    #   * close all pipe fds in the child
    stderr_target = stderr_w if not merge_streams else stdout_w
    file_actions = [
        (os.POSIX_SPAWN_DUP2, stdout_w, 1),
        (os.POSIX_SPAWN_DUP2, stderr_target, 2),
        (os.POSIX_SPAWN_CLOSE, stdout_r),
        (os.POSIX_SPAWN_CLOSE, stdout_w),
    ]
    if not merge_streams:
        file_actions.append((os.POSIX_SPAWN_CLOSE, stderr_r))
        file_actions.append((os.POSIX_SPAWN_CLOSE, stderr_w))

    def _do_spawn() -> int:
        """Execute posix_spawn in an optional cwd context."""
        if cwd and os.path.isdir(cwd):
            with _CWD_LOCK:
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
        pid = _do_spawn()
    finally:
        # Always close parent-side copies of the write ends
        try:
            os.close(stdout_w)
        except OSError:
            pass
        if not merge_streams and stderr_w is not None:
            try:
                os.close(stderr_w)
            except OSError:
                pass

    return _SpawnedProcess(pid, stdout_r, stderr_r)


# ---------------------------------------------------------------------------
# CommandExecutor (public API)
# ---------------------------------------------------------------------------

class CommandExecutor:
    """Runs commands as subprocesses and streams their output back to the caller."""

    def __init__(self, default_timeout: int = 300):
        self.default_timeout = default_timeout

    async def execute(
        self,
        cmd: str,
        args: list[str],
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        merge_streams: bool = False,
        shell: bool = False,
        load_profile: str = "false",
        on_pid: Optional[callable] = None,
    ) -> dict:
        """Convenience wrapper around :meth:`execute_stream` that buffers all output."""

        output: list[bytes] = []
        error_output: list[bytes] = []
        exit_code: Optional[int] = None
        timed_out = False

        async for event in self.execute_stream(
            cmd,
            args,
            env=env,
            cwd=cwd,
            timeout=timeout,
            merge_streams=merge_streams,
            shell=shell,
            load_profile=load_profile,
            on_pid=on_pid,
        ):
            if event.type == StreamEventType.STDOUT:
                output.append(event.data)
            elif event.type == StreamEventType.STDERR:
                error_output.append(event.data)
            elif event.type == StreamEventType.EXIT:
                exit_code = event.exit_code
            elif event.type == StreamEventType.ERROR:
                timed_out = True

        result = {
            "stdout": b"".join(output).decode(errors="replace"),
            "stderr": b"".join(error_output).decode(errors="replace"),
            "exit_code": exit_code if not timed_out else -1,
            "timed_out": timed_out,
        }
        return result

    async def execute_stream(
        self,
        cmd: str,
        args: list[str],
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        merge_streams: bool = False,
        shell: bool = False,
        load_profile: str = "false",
        on_pid: Optional[callable] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run a command and yield ``StreamEvent`` objects as it runs.

        The command is killed and a ``StreamEventType.ERROR`` event is yielded
        if it does not complete within ``timeout`` seconds. Passing ``None``
        falls back to the executor's ``default_timeout``.

        When ``shell`` is True the command is parsed by ``/bin/bash -c`` so
        shell constructs (pipes, &&, ||, redirections, ...) work as expected.

        When ``load_profile`` is set, it controls bash profile loading
        (only effective when ``shell`` is True):
          - "false" (default): ``--noprofile --norc`` — clean environment
          - "true": source ``/etc/bash.bashrc`` + ``~/.bashrc`` before the command
          - "login": source ``/etc/profile`` + ``~/.bash_profile`` (or ``~/.bash_login`` / ``~/.profile``) + ``~/.bashrc`` before the command

        Profile sourcing uses ``set +e`` to tolerate errors in startup files
        and ``2>/dev/null`` to suppress stderr noise.  The user command runs
        after ``set -e`` is restored.
        """

        effective_timeout = timeout if timeout is not None else self.default_timeout
        process: Optional[_SpawnedProcess] = None
        process_killed = False

        try:
            # -----------------------------------------------------------------
            # Decide how to start the subprocess.
            # -----------------------------------------------------------------
            if shell:
                full_line = cmd
                bash_env = os.environ.copy()
                if env:
                    bash_env.update(env)
                bash_env.pop("BASH_ENV", None)
                if load_profile == "login":
                    # NOTE: ~/.bashrc and /etc/bash.bashrc contain a
                    # ``case $- in *i*) ;; *) return;; esac`` guard that
                    # makes them return early in non-interactive shells.
                    # Using ``source`` (``.``) here would silently skip the
                    # body of those files.  We use ``eval "$(<file)"``
                    # instead: ``return`` raises an error in eval context
                    # but ``set +e`` lets execution continue, so the
                    # variables / aliases / functions defined in bashrc
                    # actually take effect.
                    prefix = (
                        'set +e; '
                        '[ -r /etc/profile ] && . /etc/profile 2>/dev/null; '
                        '[ -r ~/.bash_profile ] && . ~/.bash_profile 2>/dev/null || { '
                        '[ -r ~/.bash_login ] && . ~/.bash_login 2>/dev/null || { '
                        '[ -r ~/.profile ] && . ~/.profile 2>/dev/null; }; }; '
                        '[ -r /etc/bash.bashrc ] && eval "$(< /etc/bash.bashrc)" 2>/dev/null; '
                        '[ -r ~/.bashrc ] && eval "$(<~/.bashrc)" 2>/dev/null; '
                        'set -e; '
                    )
                    full_line = f"{prefix}{full_line}"
                    popen_argv = ["/bin/bash", "--noprofile", "--norc", "-c", full_line, "bash"] + list(args)
                elif load_profile == "true":
                    prefix = (
                        'set +e; '
                        '[ -r /etc/bash.bashrc ] && eval "$(< /etc/bash.bashrc)" 2>/dev/null; '
                        '[ -r ~/.bashrc ] && eval "$(<~/.bashrc)" 2>/dev/null; '
                        'set -e; '
                    )
                    full_line = f"{prefix}{full_line}"
                    popen_argv = ["/bin/bash", "--noprofile", "--norc", "-c", full_line, "bash"] + list(args)
                else:
                    popen_argv = ["/bin/bash", "--noprofile", "--norc", "-c", full_line, "bash"] + list(args)
                popen_env = bash_env
            else:
                popen_argv = [cmd, *args]
                popen_env = env

            # ``os.posix_spawn`` is fork-safe because it does NOT invoke
            # ``pthread_atfork`` handlers. Run it on a thread pool to avoid
            # blocking the event loop.
            def _spawn() -> _SpawnedProcess:
                return _spawn_process_via_posix_spawn(
                    popen_argv,
                    env=popen_env,
                    cwd=cwd,
                    merge_streams=merge_streams,
                )

            process = await asyncio.to_thread(_spawn)

            # Report PID to caller for signal management.
            if on_pid and process.pid:
                try:
                    on_pid(process.pid)
                except Exception:
                    logger.debug("on_pid callback raised an exception (ignored)")

            # -----------------------------------------------------------------
            # Deadline-based streaming loop.
            # -----------------------------------------------------------------
            deadline = time.monotonic() + effective_timeout
            timed_out = False
            output_gen = self._read_streams(process, merge_streams, deadline)

            try:
                async for event in output_gen:
                    if event.type == StreamEventType.ERROR:
                        timed_out = True
                    yield event

                if timed_out:
                    pgid_safe = None
                    try:
                        if process and process.pid:
                            pgid_safe = os.getpgid(process.pid)
                    except (OSError, ProcessLookupError):
                        pass
                    CommandLogger.timeout(
                        logger, effective_timeout, cmd, args,
                        pid=process.pid if process else None,
                        pgid=pgid_safe,
                    )
                    if process and process.poll() is None and not process_killed:
                        process_killed = True
                        self._terminate_process_group(process)
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        data=f"Command timed out after {effective_timeout}s".encode(),
                    )
                else:
                    # Normal completion: ensure returncode is populated.
                    if process and process.returncode is None:
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            logger.warning(
                                "Process %s did not terminate within 2s after stream EOF",
                                process.pid,
                            )
                            if not process_killed:
                                process_killed = True
                                self._terminate_process_group(process)
                    yield StreamEvent(
                        type=StreamEventType.EXIT,
                        data=b"",
                        exit_code=process.returncode if process else 0,
                    )
            finally:
                try:
                    await output_gen.aclose()
                except Exception:
                    pass

        except asyncio.CancelledError:
            CommandLogger.cancelled(
                logger, cmd, args,
                pid=process.pid if process else None,
            )
            raise
        except FileNotFoundError as e:
            CommandLogger.command_not_found(logger, cmd, args, e)
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"Command not found: {cmd}".encode(),
            )
            yield StreamEvent(type=StreamEventType.EXIT, data=b"", exit_code=127)
        except PermissionError as e:
            CommandLogger.permission_denied(logger, cmd, args, e)
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"Permission denied when executing: {cmd}".encode(),
            )
            yield StreamEvent(type=StreamEventType.EXIT, data=b"", exit_code=126)
        except Exception as e:
            CommandLogger.unexpected_error(logger, cmd, args, e)
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"An unexpected error occurred: {str(e)}".encode(),
            )
        finally:
            # Best-effort cleanup: if the generator is abandoned while the
            # process is still alive, kill it cleanly.
            if process and not process_killed:
                if process.poll() is None:
                    CommandLogger.finally_cleanup(logger, cmd, process.pid)
                    process_killed = True
                    self._terminate_process_group(process)
                    CommandLogger.finally_terminated(logger, process.pid)

    @staticmethod
    def _terminate_process_group(process: _SpawnedProcess) -> None:
        """Send SIGTERM then SIGKILL to the process group of ``process``.

        All ``os`` calls are wrapped in ``ProcessLookupError`` / ``OSError``
        guards because the process may exit between ``poll()`` and the
        ``killpg`` call, especially in gRPC-heavy environments.
        """
        if not process or not process.pid:
            return
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, OSError):
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Process %s did not terminate after SIGKILL", process.pid
                )

    async def _read_streams(
        self,
        process: _SpawnedProcess,
        merge_streams: bool = False,
        deadline: Optional[float] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Read stdout and stderr in parallel using ``asyncio.to_thread``.

        Does NOT depend on asyncio's child-watcher machinery. Instead we:
        1. Read each pipe in a separate thread pool task.
        2. Detect EOF by receiving ``b""`` from a pipe.
        3. Detect process exit via ``process.poll()``.
        4. Honor ``deadline`` by periodically waking up and checking.
        """

        stdout_done = process.stdout is None
        stderr_done = merge_streams or process.stderr is None
        pending_tasks: set[asyncio.Task] = set()

        def _read_pipe(pipe) -> bytes:
            try:
                return pipe.read(4096)
            except (OSError, ValueError):
                return b""

        try:
            while not (stdout_done and stderr_done):
                # ---------- Deadline check ----------
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        yield StreamEvent(
                            type=StreamEventType.ERROR,
                            data=b"Execution deadline exceeded",
                        )
                        return
                    iter_timeout = min(max(remaining, 0.1), 0.5)
                else:
                    iter_timeout = 0.5

                # ---------- Spawn reader tasks for pipes that aren't done ----------
                if not stdout_done and not any(
                    t.get_name() == f"stdout-{process.pid}" for t in pending_tasks
                ):
                    task = asyncio.create_task(
                        asyncio.to_thread(_read_pipe, process.stdout),
                        name=f"stdout-{process.pid}",
                    )
                    pending_tasks.add(task)
                if not stderr_done and not any(
                    t.get_name() == f"stderr-{process.pid}" for t in pending_tasks
                ):
                    task = asyncio.create_task(
                        asyncio.to_thread(_read_pipe, process.stderr),
                        name=f"stderr-{process.pid}",
                    )
                    pending_tasks.add(task)

                # ---------- If process has exited, shorten wait time ----------
                if process.poll() is not None and pending_tasks:
                    iter_timeout = min(iter_timeout, 0.1)

                if not pending_tasks:
                    await asyncio.sleep(0.01)
                    continue

                # ---------- Wait for the first completed reader ----------
                done, pending_tasks = await asyncio.wait(
                    pending_tasks,
                    timeout=iter_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # ---------- Process completed reads ----------
                for finished_task in done:
                    try:
                        data = finished_task.result()
                    except Exception:
                        data = b""

                    name = finished_task.get_name() or ""
                    if name.startswith("stdout"):
                        if not data:
                            stdout_done = True
                        else:
                            yield StreamEvent(
                                type=StreamEventType.STDOUT, data=data
                            )
                    elif name.startswith("stderr"):
                        if not data:
                            stderr_done = True
                        else:
                            yield StreamEvent(
                                type=StreamEventType.STDERR, data=data
                            )

            # Both pipes exhausted -- reap the process so returncode is set.
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Process %s: pipes closed but wait() timed out", process.pid
                )
        finally:
            for task in list(pending_tasks):
                if not task.done():
                    task.cancel()
            pending_tasks.clear()

    async def execute_command(
        self,
        command: str,
        arguments: list[str] = None,
        environment: dict[str, str] = None,
        working_directory: str = None,
        timeout_seconds: int = None,
        merge_streams: bool = False,
        shell: bool = False,
        load_profile: str = "false",
        on_pid: Optional[callable] = None,
    ):
        """Alias kept for backward compatibility with existing callers."""

        return await self.execute_stream(
            cmd=command,
            args=arguments or [],
            env=environment,
            cwd=working_directory,
            timeout=timeout_seconds,
            merge_streams=merge_streams,
            shell=shell,
            load_profile=load_profile,
            on_pid=on_pid,
        )