import asyncio
from collections.abc import AsyncGenerator
import logging
import os
import psutil
import re
import shlex
import signal
import time
import uuid

import grpc
from typing import Any, Dict
from .generated.executor.v1 import command_service_pb2
from .generated.executor.v1.command_service_pb2_grpc import ClientServiceServicer 
from .session_manager import SessionManager

from ..infra.config import settings
from ..infra.exceptions import CommandTimeoutError, CommandExecutionError
from ..executors.command_executor import CommandExecutor, StreamEventType
from ..executors.privileged_executor import PrivilegedCommandExecutor
from ..infra.logger import add_context_to_log_record, CommandLogger


from prometheus_client import Counter, Histogram

# gRPC peer formats:
#   ipv4:192.168.1.1:50051
#   ipv6:%5B::1%5D:50051          (URL-encoded [::1])
#   ipv6:%5B::ffff:192.168.1.1%5D:50051
_PEER_IP_RE = re.compile(
    r'^(?:ipv4:)?([^:]+?):(\d+)$'                   # ipv4:1.2.3.4:port
    r'|'
    r'^ipv6:%5B(.+?)%5D:(\d+)$'                     # ipv6:%5B...%5D:port
)
_IPV4_MAPPED_RE = re.compile(r'^::ffff:(\d+\.\d+\.\d+\.\d+)$')


def _parse_peer_ip(peer: str) -> str:
    """Extract and convert IP address from gRPC peer string, preserving port.

    gRPC context.peer() returns format:
      ipv4:192.168.1.1:50051
      ipv6:%5B::1%5D:50051
      ipv6:%5B::ffff:192.168.1.1%5D:50051
    This function extracts the IP and converts IPv4-mapped IPv6 to pure IPv4, while preserving port.
    """
    m = _PEER_IP_RE.match(peer)
    if not m:
        return peer
    ip = m.group(1) or m.group(3)  # group(1)=ipv4, group(3)=ipv6
    port = m.group(2) or m.group(4)
    m2 = _IPV4_MAPPED_RE.match(ip)
    ip = m2.group(1) if m2 else ip
    return f"{ip}:{port}"

# Define metrics
COMMAND_COUNTER = Counter(
    "client_commands_total", "Total commands executed", ["command", "status"]
)
COMMAND_DURATION = Histogram(
    "client_command_duration_seconds", "Time spent executing commands"
)

# In main.py, start a metrics server on a different port, e.g., 8000
# start_http_server(8000)

# In ExecuteCommand handler:
# with COMMAND_DURATION.time():
#     try:
#         # ... execution logic ...
#         COMMAND_COUNTER.labels(command=cmd, status='success').inc()
#     except Exception as e:
#         COMMAND_COUNTER.labels(command=cmd, status='failure').inc()
#         raise


logger = logging.getLogger(__name__)


class ClientServicer(ClientServiceServicer):
    def __init__(self, state_manager):
        self.state_manager = state_manager
        self.execution_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent commands
        self.executor = CommandExecutor(default_timeout=300)
        self.privileged_executor = PrivilegedCommandExecutor(default_timeout=300)
        self.session_manager = SessionManager(max_sessions=10, session_timeout=3600)
        # Track executions for signal management
        self._executions: Dict[str, Dict[str, Any]] = {}  # execution_id -> {pid, command, args, started_at, status}
        self._exec_lock = asyncio.Lock()

    def _make_pid_callback(self, execution_id: str):
        """Create a callback that records the PID for a given execution.

        ``on_pid`` is invoked synchronously from the executor's task (after
        ``create_subprocess_exec`` returns), so we mutate ``_executions``
        directly -- the write is atomic and does not need an async lock.
        """
        def _on_pid(pid: int):
            try:
                info = self._executions.get(execution_id)
                if info is not None:
                    info["pid"] = pid
            except Exception:
                logger.debug("Failed to record PID for %s", execution_id)
        return _on_pid

    def _register_execution(self, execution_id: str, cmd: str, args: list[str]):
        """Register a new execution for tracking (synchronous, atomic)."""
        self._executions[execution_id] = {
            "pid": 0,
            "command": cmd,
            "args": args,
            "started_at": time.time(),
            "status": "running",
        }

    def _clear_execution(self, execution_id: str):
        """Remove execution tracking for a completed execution (atomic)."""
        self._executions.pop(execution_id, None)

    async def ExecuteCommand(
        self,
        request: command_service_pb2.CommandRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> AsyncGenerator[command_service_pb2.CommandResponse, None]:
        """Handles the streaming command execution RPC."""
        # Add client IP to log context
        client_ip = _parse_peer_ip(context.peer())
        log_context = {
            "client_ip": client_ip
        }
        add_context_to_log_record(log_context)
        
        async with self.execution_semaphore:
            cmd = request.command
            args = list(request.args)
            logger.info(f"Received command execution request: {cmd} {' '.join(args)}")

            try:
                # Check if this is a privileged command request
                # In a real implementation, this would likely come from metadata or a separate field
                # For demo purposes, we'll look for a special environment variable
                is_privileged = request.environment.get("PRIVILEGED_EXECUTION", "false").lower() == "true"
                sudo_password = request.environment.get("SUDO_PASSWORD", "")
                su_password = request.environment.get("SU_PASSWORD", "")
                sudo_user = request.environment.get("SUDO_USER", None)
                su_user = request.environment.get("SU_USER", None)
                
                # Remove sensitive environment variables
                env = dict(request.environment)
                env.pop("PRIVILEGED_EXECUTION", None)
                env.pop("SUDO_PASSWORD", None)
                env.pop("SU_PASSWORD", None)

                # Detect whether the command relies on shell syntax (pipes, &&, ||,
                # ;, redirections, quoting, variable expansion...). When any such
                # token is present, running through a simple ``cmd`` + ``args``
                # execv call breaks the command's semantics, so we fall back to
                # /bin/bash -c execution.
                SHELL_TOKENS = ("&&", "||", "|", ";", ">", "<", "`", "$")

                def _needs_shell(command: str, arguments: list[str]) -> bool:
                    raw = " ".join([command] + arguments)
                    # presence of a shell control operator
                    for tok in SHELL_TOKENS:
                        if tok in raw:
                            return True
                    # presence of quoting (simple whitespace split would have
                    # left quote chars in the tokens)
                    if "'" in raw or '"' in raw:
                        return True
                    # command contains spaces (e.g., "ls -l") should use shell
                    if ' ' in command:
                        return True
                    return False

                # The client can explicitly request shell execution by setting
                # the __TAURUS_USE_SHELL__ flag in the environment. Otherwise we
                # auto-detect it from the command text.
                client_requested_shell = request.environment.get("__TAURUS_USE_SHELL__", "false").lower() == "true"
                # When the command is an interpreter invoked with -c (e.g.
                # "/bin/bash -c <script>"), the args contain the full script
                # body which may include quotes, pipes, etc.  Those tokens are
                # part of the script content, NOT shell operators that require
                # us to wrap the command in another bash -c layer.  Detect this
                # pattern and skip the auto-detection to avoid mangling the
                # script into separate positional arguments.
                is_interpreter_dash_c = (
                    not client_requested_shell
                    and len(args) >= 2
                    and args[0] == "-c"
                    and os.path.basename(cmd) in ("bash", "sh", "python", "python3", "python3.12")
                )
                if is_interpreter_dash_c:
                    use_shell = False
                else:
                    use_shell = client_requested_shell or _needs_shell(cmd, args)
                env.pop("__TAURUS_USE_SHELL__", None)
                load_profile = env.pop("__TAURUS_LOAD_PROFILE__", "false").lower()
                
                # Create an event to monitor if client disconnects
                cancellation_event = asyncio.Event()
                # Use mutable container to share state in closure and done_callback (avoid nonlocal declaration issues)
                execution_completed = [False]

                def on_client_done(ctx):
                    """Callback invoked when RPC completes.
                    Only treat as client disconnect when status is CANCELLED and command is still running,
                    normal completion (OK) should not produce false positive logs.
                    """
                    code = None
                    try:
                        code = ctx.code()
                    except Exception:
                        pass
                    # Only log disconnect for actual client cancellation (CANCELLED)
                    if code == grpc.StatusCode.CANCELLED and not execution_completed[0]:
                        cancellation_event.set()
                        CommandLogger.client_disconnect(logger, cmd, args)
                    else:
                        # Normal completion or other error states, only set event to wake up possible loops
                        cancellation_event.set()

                # Register cancel callback
                context.add_done_callback(on_client_done)

                with COMMAND_DURATION.time():
                    # Use the execution_id from the client if provided, otherwise generate one
                    execution_id = env.pop("__TAURUS_EXECUTION_ID__", None) or str(uuid.uuid4())
                    pid_callback = self._make_pid_callback(execution_id)
                    self._register_execution(execution_id, cmd, args)
                    # When running /bin/bash -c '<script>' directly
                    # (is_interpreter_dash_c), load_profile would be ignored
                    # because shell=False and execute_stream only applies
                    # profile sourcing in shell mode.  Inject the
                    # profile-sourcing prefix into the script body so that
                    # bashrc/profile variables are available inside the script.
                    #
                    # NOTE: ~/.bashrc and /etc/bash.bashrc contain a
                    # ``case $- in *i*) ;; *) return;; esac`` guard that
                    # makes them return early in non-interactive shells.
                    # Using ``source`` (``.``) would silently skip the body.
                    # We use ``eval "$(<file)"`` instead: ``return`` raises
                    # an error in eval context but ``set +e`` lets execution
                    # continue, so bashrc content actually takes effect.
                    if is_interpreter_dash_c and load_profile in ("true", "login") \
                            and os.path.basename(cmd) in ("bash", "sh"):
                        if load_profile == "login":
                            profile_prefix = (
                                'set +e; '
                                '[ -r /etc/profile ] && . /etc/profile 2>/dev/null; '
                                '[ -r ~/.bash_profile ] && . ~/.bash_profile 2>/dev/null || { '
                                '[ -r ~/.bash_login ] && . ~/.bash_login 2>/dev/null || { '
                                '[ -r ~/.profile ] && . ~/.profile 2>/dev/null; }; }; '
                                '[ -r /etc/bash.bashrc ] && eval "$(< /etc/bash.bashrc)" 2>/dev/null; '
                                '[ -r ~/.bashrc ] && eval "$(<~/.bashrc)" 2>/dev/null; '
                                'set -e; '
                            )
                        else:
                            profile_prefix = (
                                'set +e; '
                                '[ -r /etc/bash.bashrc ] && eval "$(< /etc/bash.bashrc)" 2>/dev/null; '
                                '[ -r ~/.bashrc ] && eval "$(<~/.bashrc)" 2>/dev/null; '
                                'set -e; '
                            )
                        args = list(args)
                        args[1] = profile_prefix + args[1]
                    if is_privileged and sudo_password:
                        # Execute with provided password (least secure option)
                        event_generator = self.privileged_executor.execute_with_sudo_prompt(
                            cmd=cmd,
                            args=args,
                            sudo_password=sudo_password,
                            env=env,
                            cwd=request.working_directory or os.getcwd(),
                            timeout=request.timeout_seconds,
                            merge_streams=request.merge_streams,
                            on_pid=pid_callback,
                        )
                    elif is_privileged and su_password and su_user:
                        # Execute with su and provided password (also less secure option)
                        event_generator = self.privileged_executor.execute_with_su_prompt(
                            cmd=cmd,
                            args=args,
                            target_user=su_user,
                            user_password=su_password,
                            env=env,
                            cwd=request.working_directory or os.getcwd(),
                            timeout=request.timeout_seconds,
                            merge_streams=request.merge_streams,
                            on_pid=pid_callback,
                        )
                    elif is_privileged and not sudo_password:
                        # Execute with NOPASSWD sudo (recommended)
                        event_generator = self.privileged_executor.execute_with_specific_sudo_nopasswd(
                            cmd=cmd,
                            args=args,
                            sudo_user=sudo_user,
                            env=env,
                            cwd=request.working_directory or os.getcwd(),
                            timeout=request.timeout_seconds,
                            merge_streams=request.merge_streams,
                            on_pid=pid_callback,
                        )
                    else:
                        # Standard execution
                        if use_shell:
                            # When shell semantics are required we let /bin/bash
                            # parse the full command line via -c.  Pass cmd verbatim
                            # as the script body and forward `args` as positional
                            # parameters ($1, $2, ...) so multi-line scripts and
                            # env vars behave as users expect.
                            event_generator = self.executor.execute_stream(
                                cmd=cmd,
                                args=args,
                                env=env,
                                cwd=request.working_directory or os.getcwd(),
                                timeout=request.timeout_seconds,
                                merge_streams=request.merge_streams,
                                shell=True,
                                load_profile=load_profile,
                                on_pid=pid_callback,
                            )
                        else:
                            event_generator = self.executor.execute_stream(
                                cmd=cmd,
                                args=args,
                                env=env,
                                cwd=request.working_directory or os.getcwd(),
                                timeout=request.timeout_seconds,
                                merge_streams=request.merge_streams,
                                on_pid=pid_callback,
                            )

                    # Iterate both event generator and monitor cancellation event
                    async for event in event_generator:
                        # Check for cancellation request
                        if cancellation_event.is_set():
                            # If client disconnected, terminate command execution
                            CommandLogger.terminate_by_disconnect(logger, cmd, args)
                            # Actively close generator to trigger command_executor's finally block to kill process
                            await event_generator.aclose()
                            yield command_service_pb2.CommandResponse(
                                error_message="Client disconnected, command execution cancelled"
                            )
                            break

                        if event.type == StreamEventType.STDOUT:
                            yield command_service_pb2.CommandResponse(stdout_chunk=event.data)
                            COMMAND_COUNTER.labels(command=cmd, status='success').inc()
                        elif event.type == StreamEventType.STDERR:
                            yield command_service_pb2.CommandResponse(stderr_chunk=event.data)
                            COMMAND_COUNTER.labels(command=cmd, status='success').inc()
                        elif event.type == StreamEventType.ERROR:
                            COMMAND_COUNTER.labels(command=cmd, status='failure').inc()
                            yield command_service_pb2.CommandResponse(error_message=event.data.decode())
                        elif event.type == StreamEventType.EXIT:
                            # Send a final message indicating completion with exit code
                            yield command_service_pb2.CommandResponse(finished=True, exit_code=event.exit_code)

                    # Clean up PID tracking after execution completes
                    self._clear_execution(execution_id)

            except (CommandTimeoutError, CommandExecutionError) as e:
                logger.error(f"Command failed: {e}")
                COMMAND_COUNTER.labels(command=cmd, status='failure').inc()
                yield command_service_pb2.CommandResponse(
                    finished=True, error_message=str(e), exit_code=1
                )
            except Exception as e:
                logger.critical(
                    f"An unexpected error occurred in ExecuteCommand: {e}",
                    exc_info=True,
                )
                COMMAND_COUNTER.labels(command=cmd, status='failure').inc()
                # Use context.set_code to set the gRPC status code
                await context.abort(
                    grpc.StatusCode.INTERNAL, f"An internal server error occurred: {e}"
                )
            finally:
                # Whether normal completion or exception, mark execution as completed to prevent done_callback from triggering disconnect log
                execution_completed[0] = True

    async def GetStatus(
        self,
        request: command_service_pb2.StatusRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.StatusResponse:
        """Handles the GetStatus RPC."""
        client_ip = _parse_peer_ip(context.peer())
        log_context = {
            "client_ip": client_ip
        }
        add_context_to_log_record(log_context)
        
        logger.info("Received status request.")
        
        # Get enhanced system status
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        load_avg = list(os.getloadavg()) if hasattr(os, 'getloadavg') else [0.0, 0.0, 0.0]
        
        # Log extended status
        logger.debug(
            f"Status: cpu={psutil.cpu_percent(interval=0.5)}%, "
            f"mem={psutil.virtual_memory().percent}%, "
            f"disk={psutil.disk_usage('/').percent}%, "
            f"load={load_avg}"
        )
        
        return command_service_pb2.StatusResponse(
            version=settings.current_version,
            uptime=f"{uptime_seconds:.2f}s",
            hostname=os.uname().nodename,
            cpu_usage=psutil.cpu_percent(interval=0.5),
            memory_usage=psutil.virtual_memory().percent,
            disk_usage=psutil.disk_usage('/').percent,
            load_avg=load_avg,
        )

    async def SendSignal(
        self,
        request: command_service_pb2.SendSignalRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.SendSignalResponse:
        """Send a signal to a running process by PID."""
        pid = request.pid
        sig = request.signal
        client_ip = _parse_peer_ip(context.peer())
        log_context = {"client_ip": client_ip}
        add_context_to_log_record(log_context)

        logger.info(f"Received SendSignal request: pid={pid}, signal={sig}")

        if pid <= 0:
            return command_service_pb2.SendSignalResponse(
                success=False, message=f"Invalid PID: {pid}"
            )

        try:
            # Send signal to the entire process group (-pid) to kill all children
            os.killpg(pid, sig)
            sig_name = signal.Signals(sig).name if sig in [s.value for s in signal.Signals] else str(sig)
            logger.info(f"Sent signal {sig_name} to process group {pid}")

            # Update execution status
            if sig == signal.SIGSTOP:
                new_status = "paused"
            elif sig == signal.SIGCONT:
                new_status = "running"
            else:
                new_status = None

            if new_status:
                async with self._exec_lock:
                    for info in self._executions.values():
                        if info["pid"] == pid:
                            info["status"] = new_status
                            break

            return command_service_pb2.SendSignalResponse(
                success=True,
                message=f"Signal {sig_name} sent to process group {pid}"
            )
        except ProcessLookupError:
            return command_service_pb2.SendSignalResponse(
                success=False, message=f"Process group {pid} not found"
            )
        except PermissionError:
            return command_service_pb2.SendSignalResponse(
                success=False, message=f"Permission denied to send signal to process group {pid}"
            )
        except Exception as e:
            logger.error(f"Failed to send signal: {e}", exc_info=True)
            return command_service_pb2.SendSignalResponse(
                success=False, message=str(e)
            )

    async def ListExecutions(
        self,
        request: command_service_pb2.ListExecutionsRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.ListExecutionsResponse:
        """List all currently executing commands."""
        client_ip = _parse_peer_ip(context.peer())
        log_context = {"client_ip": client_ip}
        add_context_to_log_record(log_context)

        logger.info("Received ListExecutions request")

        async with self._exec_lock:
            executions = []
            for exec_id, info in self._executions.items():
                executions.append(command_service_pb2.ExecutionInfo(
                    execution_id=exec_id,
                    command=info["command"],
                    args=info["args"],
                    pid=info["pid"],
                    status=info["status"],
                    started_at=int(info["started_at"]),
                ))

        return command_service_pb2.ListExecutionsResponse(executions=executions)

    async def Maintenance(
        self,
        request: command_service_pb2.MaintenanceRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.MaintenanceResponse:
        """Handles maintenance operations like restart."""
        # Add client IP to log context
        client_ip = _parse_peer_ip(context.peer())
        log_context = {
            "client_ip": client_ip
        }
        add_context_to_log_record(log_context)
        
        logger.info(f"Received maintenance request: {request.WhichOneof('action')}")

        action = request.WhichOneof("action")
        if action == "restart":
            # Trigger restart via SIGUSR1 signal
            logger.info("Triggering restart via SIGUSR1...")
            os.kill(os.getpid(), signal.SIGUSR1)
            return command_service_pb2.MaintenanceResponse(
                success=True, message="Restart process initiated."
            )

        return command_service_pb2.MaintenanceResponse(
            success=False, message="Unknown maintenance action."
        )

    async def CreateSession(
        self,
        request: command_service_pb2.SessionRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.SessionResponse:
        """Create interactive session"""
        client_ip = _parse_peer_ip(context.peer())
        log_context = {"client_ip": client_ip}
        add_context_to_log_record(log_context)
        
        logger.info(f"Creating session for user: {request.username or 'current'}")
        
        try:
            session_id = await self.session_manager.create_session(
                username=request.username if request.username else None,
                password=request.password if request.password else None,
                working_directory=request.working_directory if request.working_directory else None,
                environment=dict(request.environment) if request.environment else None,
                shell=request.shell if request.shell else "/bin/bash",
                timeout=request.timeout_seconds if request.timeout_seconds > 0 else 3600,
            )
            
            if session_id:
                return command_service_pb2.SessionResponse(
                    session_id=session_id,
                    success=True,
                    message="Session created successfully"
                )
            else:
                return command_service_pb2.SessionResponse(
                    session_id="",
                    success=False,
                    message="Failed to create session or maximum sessions reached"
                )
                
        except PermissionError as e:
            logger.warning(f"Permission denied creating session: {e}")
            return command_service_pb2.SessionResponse(
                session_id="",
                success=False,
                message=f"Authentication failed: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error creating session: {e}", exc_info=True)
            return command_service_pb2.SessionResponse(
                session_id="",
                success=False,
                message=f"Internal error: {str(e)}"
            )

    async def ExecuteInSession(
        self,
        request: command_service_pb2.SessionCommandRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> AsyncGenerator[command_service_pb2.CommandResponse, None]:
        """Execute command in session"""
        client_ip = _parse_peer_ip(context.peer())
        log_context = {"client_ip": client_ip, "session_id": request.session_id}
        add_context_to_log_record(log_context)
        
        logger.info(f"Executing command in session {request.session_id}: {request.command}")
        
        try:
            async for event in self.session_manager.execute_in_session(
                session_id=request.session_id,
                command=request.command,
                timeout=request.timeout_seconds if request.timeout_seconds > 0 else 30,
            ):
                if event.type == StreamEventType.STDOUT:
                    yield command_service_pb2.CommandResponse(stdout_chunk=event.data)
                elif event.type == StreamEventType.STDERR:
                    yield command_service_pb2.CommandResponse(stderr_chunk=event.data)
                elif event.type == StreamEventType.EXIT:
                    yield command_service_pb2.CommandResponse(
                        finished=True,
                        exit_code=event.exit_code if event.exit_code is not None else 0
                    )
                elif event.type == StreamEventType.ERROR:
                    yield command_service_pb2.CommandResponse(error_message=event.data.decode())
                    
        except KeyError as e:
            logger.warning(f"Session not found: {e}")
            yield command_service_pb2.CommandResponse(
                finished=True,
                error_message=f"Session not found: {str(e)}"
            )
        except RuntimeError as e:
            logger.warning(f"Session error: {e}")
            yield command_service_pb2.CommandResponse(
                finished=True,
                error_message=str(e)
            )
        except Exception as e:
            logger.error(f"Error executing in session: {e}", exc_info=True)
            yield command_service_pb2.CommandResponse(
                finished=True,
                error_message=f"Internal error: {str(e)}"
            )

    async def CloseSession(
        self,
        request: command_service_pb2.SessionCloseRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.SessionCloseResponse:
        """Close session"""
        client_ip = _parse_peer_ip(context.peer())
        log_context = {"client_ip": client_ip, "session_id": request.session_id}
        add_context_to_log_record(log_context)
        
        logger.info(f"Closing session {request.session_id}")
        
        try:
            success = await self.session_manager.close_session(request.session_id)
            return command_service_pb2.SessionCloseResponse(
                success=success,
                message="Session closed" if success else "Session not found"
            )
        except Exception as e:
            logger.error(f"Error closing session: {e}", exc_info=True)
            return command_service_pb2.SessionCloseResponse(
                success=False,
                message=f"Error: {str(e)}"
            )

    async def ListSessions(
        self,
        request: command_service_pb2.ListSessionsRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.ListSessionsResponse:
        """List all active sessions"""
        client_ip = _parse_peer_ip(context.peer())
        log_context = {"client_ip": client_ip}
        add_context_to_log_record(log_context)
        
        logger.info("Listing active sessions")
        
        try:
            sessions = self.session_manager.list_sessions()
            session_infos = []
            
            for s in sessions:
                session_infos.append(command_service_pb2.SessionInfo(
                    session_id=s["session_id"],
                    username=s["username"],
                    working_directory=s["working_directory"],
                    created_at=int(s["created_at"]),
                    last_active=int(s["last_active"]),
                    is_alive=s["is_alive"],
                ))
            
            return command_service_pb2.ListSessionsResponse(sessions=session_infos)
            
        except Exception as e:
            logger.error(f"Error listing sessions: {e}", exc_info=True)
            return command_service_pb2.ListSessionsResponse(sessions=[])
