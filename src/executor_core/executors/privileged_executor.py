#!/usr/bin/env python3
"""
Privileged command executor for safely running commands that need elevated perms.

This module implements secure mechanisms for executing privileged commands
without directly transmitting passwords over the network.
"""

import asyncio
import os
import re
import signal
import logging
import subprocess
import time
import pexpect
from typing import AsyncGenerator, List, Optional, Dict, Any

from .command_executor import (
    CommandExecutor,
    StreamEvent,
    StreamEventType,
    _SpawnedProcess,
    _resolve_in_path,
    _spawn_process_via_posix_spawn,
)

logger = logging.getLogger(__name__)


class PrivilegedCommandExecutor:
    """A specialized executor for running commands that need elevated privileges."""

    def __init__(self, default_timeout: int = 300):
        self.base_executor = CommandExecutor(default_timeout)
        self.default_timeout = default_timeout

    async def execute_with_sudo_prompt(
        self,
        cmd: str,
        args: list[str],
        sudo_password: str,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        merge_streams: bool = False,
        on_pid: Optional[callable] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute a command with sudo, reading the password from stdin.

        This is a fallback for environments where passwordless sudo (the
        preferred approach) is not feasible.  The password is never logged.
        """

        effective_timeout = timeout or self.default_timeout
        process: Optional[_SpawnedProcess] = None
        process_killed = False
        stdin_w_fd: Optional[int] = None

        logger.info("Executing privileged command: %s", cmd)

        full_cmd = ["sudo", "-S", cmd, *args]

        try:
            # Create stdin pipe for password delivery, then use os.posix_spawn
            # which avoids triggering gRPC's pthread_atfork handlers.
            stdin_r, stdin_w = os.pipe()
            stdin_w_fd = stdin_w

            def _spawn_with_stdin() -> _SpawnedProcess:
                """Spawn ``sudo -S ...`` using posix_spawn, redirecting stdin
                to ``stdin_r`` so we can feed the password from the parent."""
                env_dict = {**os.environ, **(env or {})} if env else os.environ.copy()
                resolved_exe = _resolve_in_path(full_cmd[0], env_dict)
                if resolved_exe is None:
                    raise FileNotFoundError(f"Command not found: {full_cmd[0]}")
                exec_argv = [resolved_exe, *full_cmd[1:]]

                stdout_r_fd, stdout_w_fd = os.pipe()
                if merge_streams:
                    stderr_r_fd, stderr_w_fd = None, None
                else:
                    stderr_r_fd, stderr_w_fd = os.pipe()

                stderr_target_fd = stderr_w_fd if not merge_streams else stdout_w_fd
                file_actions = [
                    (os.POSIX_SPAWN_DUP2, stdin_r, 0),
                    (os.POSIX_SPAWN_DUP2, stdout_w_fd, 1),
                    (os.POSIX_SPAWN_DUP2, stderr_target_fd, 2),
                    (os.POSIX_SPAWN_CLOSE, stdin_r),
                    (os.POSIX_SPAWN_CLOSE, stdin_w),
                    (os.POSIX_SPAWN_CLOSE, stdout_r_fd),
                    (os.POSIX_SPAWN_CLOSE, stdout_w_fd),
                ]
                if not merge_streams:
                    file_actions.append((os.POSIX_SPAWN_CLOSE, stderr_r_fd))
                    file_actions.append((os.POSIX_SPAWN_CLOSE, stderr_w_fd))

                def _do_spawn() -> int:
                    if cwd and os.path.isdir(cwd):
                        old_cwd_saved = os.getcwd()
                        os.chdir(cwd)
                        try:
                            return os.posix_spawn(
                                exec_argv[0], exec_argv, env_dict,
                                file_actions=file_actions, setpgroup=0,
                            )
                        finally:
                            os.chdir(old_cwd_saved)
                    return os.posix_spawn(
                        exec_argv[0], exec_argv, env_dict,
                        file_actions=file_actions, setpgroup=0,
                    )

                try:
                    pid = _do_spawn()
                finally:
                    try:
                        os.close(stdin_r)
                    except OSError:
                        pass
                    try:
                        os.close(stdout_w_fd)
                    except OSError:
                        pass
                    if not merge_streams and stderr_w_fd is not None:
                        try:
                            os.close(stderr_w_fd)
                        except OSError:
                            pass

                return _SpawnedProcess(pid, stdout_r_fd, stderr_r_fd)

            process = await asyncio.to_thread(_spawn_with_stdin)
            stdin_w_fd = None  # ownership transferred to the process

            if on_pid and process.pid:
                try:
                    on_pid(process.pid)
                except Exception:
                    logger.debug("on_pid callback raised an exception (ignored)")

            # Write password to the stdin pipe via thread pool.
            def _write_pw_to_pipe() -> None:
                try:
                    pw_bytes = (sudo_password + "\n").encode()
                    os.write(stdin_w, pw_bytes)
                except OSError:
                    pass
                finally:
                    try:
                        os.close(stdin_w)
                    except OSError:
                        pass

            pw_task = asyncio.create_task(asyncio.to_thread(_write_pw_to_pipe))

            # Deadline-driven streaming loop using the posix_spawn-friendly reader.
            deadline = time.monotonic() + effective_timeout
            timed_out = False
            output_gen = self.base_executor._read_streams(
                process, merge_streams, deadline
            )

            try:
                async for event in output_gen:
                    if event.type == StreamEventType.ERROR:
                        timed_out = True
                    elif event.type in (StreamEventType.STDOUT, StreamEventType.STDERR):
                        sanitized_data = event.data.replace(
                            sudo_password.encode(), b"***PASSWORD_HIDDEN***"
                        )
                        yield StreamEvent(
                            type=event.type,
                            data=sanitized_data,
                            exit_code=event.exit_code,
                        )
                        continue
                    yield event

                try:
                    await asyncio.wait_for(pw_task, timeout=1.0)
                except (asyncio.TimeoutError, Exception):
                    pass

                if timed_out:
                    logger.warning(
                        "Privileged command timed out after %ss: %s",
                        effective_timeout, cmd,
                    )
                    if process and process.poll() is None and not process_killed:
                        process_killed = True
                        try:
                            pgid = os.getpgid(process.pid)
                            os.killpg(pgid, signal.SIGTERM)
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                try:
                                    os.killpg(pgid, signal.SIGKILL)
                                except (ProcessLookupError, OSError):
                                    pass
                                try:
                                    process.wait(timeout=2)
                                except subprocess.TimeoutExpired:
                                    logger.warning(
                                        "Process %s did not terminate after SIGKILL",
                                        process.pid,
                                    )
                        except (ProcessLookupError, OSError):
                            pass
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        data=f"Command timed out after {effective_timeout}s".encode(),
                    )
                else:
                    if process and process.returncode is None:
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            logger.warning(
                                "Process %s did not terminate within 2s after stream EOF",
                                process.pid,
                            )
                    yield StreamEvent(
                        type=StreamEventType.EXIT,
                        data=b"",
                        exit_code=(
                            process.returncode
                            if process and process.returncode is not None
                            else 0
                        ),
                    )
            finally:
                try:
                    await output_gen.aclose()
                except Exception:
                    pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Unexpected error running privileged command")
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"An unexpected error occurred: {str(e)}".encode(),
            )
        finally:
            if stdin_w_fd is not None:
                try:
                    os.close(stdin_w_fd)
                except OSError:
                    pass
            if process and not process_killed:
                if process.poll() is None:
                    try:
                        pgid = os.getpgid(process.pid)
                        os.killpg(pgid, signal.SIGKILL)
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            logger.warning(
                                "Process %s did not terminate after SIGKILL",
                                process.pid,
                            )
                    except (ProcessLookupError, OSError):
                        pass

    async def _read_popen_streams(
        self,
        process: subprocess.Popen,
        merge_streams: bool = False,
        deadline: Optional[float] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Read stdout/stderr of a ``subprocess.Popen`` via thread pool.

        Avoids asyncio's child-watcher machinery, which is incompatible
        with gRPC fork handlers.
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

                if not stdout_done and not any(
                    t.get_name() == f"pstdout-{process.pid}" for t in pending_tasks
                ):
                    task = asyncio.create_task(
                        asyncio.to_thread(_read_pipe, process.stdout),
                        name=f"pstdout-{process.pid}",
                    )
                    pending_tasks.add(task)
                if not stderr_done and not any(
                    t.get_name() == f"pstderr-{process.pid}" for t in pending_tasks
                ):
                    task = asyncio.create_task(
                        asyncio.to_thread(_read_pipe, process.stderr),
                        name=f"pstderr-{process.pid}",
                    )
                    pending_tasks.add(task)

                if process.poll() is not None and pending_tasks:
                    iter_timeout = min(iter_timeout, 0.1)

                if not pending_tasks:
                    await asyncio.sleep(0.01)
                    continue

                done, pending_tasks = await asyncio.wait(
                    pending_tasks,
                    timeout=iter_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for finished_task in done:
                    try:
                        data = finished_task.result()
                    except Exception:
                        data = b""

                    name = finished_task.get_name() or ""
                    if name.startswith("pstdout"):
                        if not data:
                            stdout_done = True
                        else:
                            yield StreamEvent(
                                type=StreamEventType.STDOUT, data=data
                            )
                    elif name.startswith("pstderr"):
                        if not data:
                            stderr_done = True
                        else:
                            yield StreamEvent(
                                type=StreamEventType.STDERR, data=data
                            )

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

    async def _read_streams(
        self,
        process: asyncio.subprocess.Process,
        merge_streams: bool = False,
        deadline: Optional[float] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Read stdout and stderr concurrently, respecting an overall deadline."""

        stdout_task: Optional[asyncio.Task] = None
        stderr_task: Optional[asyncio.Task] = None

        stdout_done = process.stdout is None
        stderr_done = merge_streams or process.stderr is None

        try:
            while not (stdout_done and stderr_done):
                remaining: Optional[float] = None
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        yield StreamEvent(
                            type=StreamEventType.ERROR,
                            data=b"Execution deadline exceeded",
                        )
                        return

                pending: list[asyncio.Task] = []

                if not stdout_done and stdout_task is None:
                    stdout_task = asyncio.create_task(process.stdout.read(65536))
                    pending.append(stdout_task)

                if not stderr_done and stderr_task is None:
                    stderr_task = asyncio.create_task(process.stderr.read(65536))
                    pending.append(stderr_task)

                if not pending:
                    await asyncio.sleep(0)
                    continue

                if remaining is None:
                    done, _pending = await asyncio.wait(
                        pending, return_when=asyncio.FIRST_COMPLETED
                    )
                else:
                    iter_timeout = min(max(remaining, 0.1), 1.0)
                    done, _pending = await asyncio.wait(
                        pending,
                        timeout=iter_timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        continue

                for finished_task in done:
                    try:
                        data = finished_task.result()
                    except Exception:
                        data = b""

                    if finished_task is stdout_task:
                        stdout_task = None
                        if not data:
                            stdout_done = True
                        else:
                            yield StreamEvent(
                                type=StreamEventType.STDOUT, data=data
                            )
                    elif finished_task is stderr_task:
                        stderr_task = None
                        if not data:
                            stderr_done = True
                        else:
                            yield StreamEvent(
                                type=StreamEventType.STDERR, data=data
                            )
        finally:
            for task in (stdout_task, stderr_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

    async def execute_with_su_prompt(
        self,
        cmd: str,
        args: list[str],
        target_user: str,
        user_password: str,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        merge_streams: bool = False,
        on_pid: Optional[callable] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute a command using ``su -`` with PTY-based interaction (no fork).

        Uses ``os.posix_spawn`` (NOT ``fork()``) to create a child process
        attached to a pseudo-terminal, then interacts with the password prompt
        via the PTY master side. This entirely avoids gRPC's pthread_atfork
        handlers.
        """

        effective_timeout = timeout or self.default_timeout
        child = None
        process_killed = False

        logger.info("Executing command with su - %s: %s", target_user, cmd)

        try:
            full_cmd = f"{cmd} {' '.join(args)}"
            su_argv = ["su", "-", target_user, "-c", full_cmd]

            # Spawn su process with PTY using posix_spawn (no fork involved)
            from ..services.session_manager import _spawn_pty_process, _PTYShell

            master_fd, pid = _spawn_pty_process(
                su_argv,
                env=env,
                cwd=cwd,
            )
            child = _PTYShell(master_fd, pid)

            if on_pid and pid:
                try:
                    on_pid(pid)
                except Exception:
                    logger.debug("on_pid callback raised an exception (ignored)")

            # Handle password prompt from the PTY
            # Chinese terms: 密码 (password), 口令 (passphrase) - for matching Chinese system prompts
            password_prompt_re = re.compile(
                r'[Pp]assword|[Pp]assphrase|密码|口令',
                re.IGNORECASE,
            )

            try:
                idx = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: child.expect([password_prompt_re], timeout=10),
                )
            except Exception:
                idx = -1

            if idx == 0:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: child.sendline(user_password)
                    )
                except Exception:
                    pass

            # Stream output until the process exits
            deadline = time.monotonic() + effective_timeout
            exit_code = 0

            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        yield StreamEvent(
                            type=StreamEventType.ERROR,
                            data=f"Command timed out after {effective_timeout}s".encode(),
                        )
                        return

                    data = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: child.read_nonblocking(
                            size=4096, timeout=min(max(remaining, 0.5), 2.0)
                        ),
                    )

                    if data:
                        sanitized = data.replace(
                            user_password, "***PASSWORD_HIDDEN***"
                        )
                        yield StreamEvent(
                            type=StreamEventType.STDOUT,
                            data=sanitized.encode(),
                        )
                    elif not child.isalive():
                        # Process exited with no more data
                        break

                # Collect exit code
                try:
                    _, status = os.waitpid(pid, os.WNOHANG)
                    if os.WIFEXITED(status):
                        exit_code = os.WEXITSTATUS(status)
                    elif os.WIFSIGNALED(status):
                        exit_code = -os.WTERMSIG(status)
                except (ChildProcessError, OSError):
                    pass

                yield StreamEvent(
                    type=StreamEventType.EXIT, data=b"", exit_code=exit_code
                )
            finally:
                if child and child.isalive():
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    process_killed = True
                if child:
                    try:
                        child.close(force=True)
                    except Exception:
                        pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Unexpected error in su execution")
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"An unexpected error occurred: {str(e)}".encode(),
            )
        finally:
            if child and not process_killed:
                try:
                    if child.isalive():
                        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                    child.close(force=True)
                except Exception:
                    pass

    async def execute_with_specific_sudo_nopasswd(
        self,
        cmd: str,
        args: list[str],
        sudo_user: Optional[str] = None,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        merge_streams: bool = False,
        on_pid: Optional[callable] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute a command using passwordless sudo (recommended approach)."""

        sudo_args = ["sudo"]
        if sudo_user:
            sudo_args.extend(["-u", sudo_user])
        sudo_args.append(cmd)
        sudo_args.extend(args)

        logger.info(
            "Executing privileged command with NOPASSWD: %s", " ".join(sudo_args)
        )

        async for event in self.base_executor.execute_stream(
            sudo_args[0],
            sudo_args[1:],
            env=env,
            cwd=cwd,
            timeout=timeout,
            merge_streams=merge_streams,
            on_pid=on_pid,
        ):
            yield event

    async def execute_with_custom_privilege_tool(
        self,
        cmd: str,
        args: list[str],
        privilege_tool_config: Dict[str, Any],
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        merge_streams: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute a command using a custom privilege escalation tool."""

        effective_timeout = timeout or self.default_timeout

        tool_name = privilege_tool_config.get("tool_name", "custom_sudo")
        tool_args = privilege_tool_config.get("tool_args", [])

        full_cmd = [tool_name] + tool_args + [cmd] + args

        logger.info(
            "Executing privileged command with custom tool: %s", " ".join(full_cmd)
        )

        async for event in self.base_executor.execute_stream(
            full_cmd[0],
            full_cmd[1:],
            env=env,
            cwd=cwd,
            timeout=effective_timeout,
            merge_streams=merge_streams,
        ):
            yield event