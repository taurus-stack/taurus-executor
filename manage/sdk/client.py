import asyncio
import grpc
import os
from typing import AsyncGenerator, Optional, Tuple
import logging
import pathlib

from .generated.executor.v1 import command_service_pb2
from .generated.executor.v1 import command_service_pb2_grpc

logger = logging.getLogger(__name__)


def find_default_certificates() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Automatically detect default certificate paths

    Detection order:
    1. Directory specified by TAURUS_CERT_DIR environment variable
    2. tls/ directory under current directory (web_ui.py style: webui-client.crt/key)
    3. certs/sdk/ directory under current directory (SDK style: client.crt/key)
    4. Search upward for tls/ or certs/sdk/ directories in parent directories

    Returns:
        Tuple[cert_file, key_file, ca_file]: Certificate file paths, or None for each if not found
    """
    # Possible client certificate filenames
    client_cert_names = ["webui-client.crt", "client.crt", "sdk-client.crt"]
    client_key_names = ["webui-client.key", "client.key", "sdk-client.key"]
    ca_cert_names = ["ca.crt", "ca-cert.crt"]

    def find_certs_in_dir(cert_dir: pathlib.Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Find certificate files in the specified directory"""
        if not cert_dir.exists():
            return None, None, None

        # Find client certificate
        cert_file = None
        for name in client_cert_names:
            path = cert_dir / name
            if path.exists():
                cert_file = str(path)
                break

        # Find client private key
        key_file = None
        for name in client_key_names:
            path = cert_dir / name
            if path.exists():
                key_file = str(path)
                break

        # Find CA certificate
        ca_file = None
        for name in ca_cert_names:
            path = cert_dir / name
            if path.exists():
                ca_file = str(path)
                break

        return cert_file, key_file, ca_file

    def has_all_certs(result: Tuple[Optional[str], Optional[str], Optional[str]]) -> bool:
        """Check if all required certificates were found"""
        return all(result)

    # 1. Check environment variable
    env_cert_dir = os.environ.get('TAURUS_CERT_DIR')
    if env_cert_dir:
        cert_dir = pathlib.Path(env_cert_dir)
        result = find_certs_in_dir(cert_dir)
        if has_all_certs(result):
            return result

    # 2. Check tls/ under current directory (web_ui.py style)
    current_dir = pathlib.Path.cwd()
    tls_dir = current_dir / "tls"
    result = find_certs_in_dir(tls_dir)
    if has_all_certs(result):
        return result

    # 3. Check certs/sdk/ under current directory (SDK style)
    sdk_dir = current_dir / "certs" / "sdk"
    result = find_certs_in_dir(sdk_dir)
    if has_all_certs(result):
        return result

    # 4. Search upward through parent directories
    search_dir = current_dir
    while search_dir != search_dir.parent:
        # Check tls/ directory
        tls_dir = search_dir / "tls"
        result = find_certs_in_dir(tls_dir)
        if has_all_certs(result):
            return result

        # Check certs/sdk/ directory
        sdk_dir = search_dir / "certs" / "sdk"
        result = find_certs_in_dir(sdk_dir)
        if has_all_certs(result):
            return result

        search_dir = search_dir.parent

    # No certificates found
    return None, None, None


class Session:
    """Interactive session client"""
    
    def __init__(self, session_id: str, stub, metadata):
        self.session_id = session_id
        self.stub = stub
        self.metadata = metadata
        self.is_active = True
    
    async def execute(
        self,
        command: str,
        timeout: int = 30
    ) -> AsyncGenerator[dict, None]:
        """Execute command in session"""
        if not self.is_active:
            raise RuntimeError("Session is closed")
        
        request = command_service_pb2.SessionCommandRequest(
            session_id=self.session_id,
            command=command,
            timeout_seconds=timeout
        )
        
        response_stream = self.stub.ExecuteInSession(request, metadata=self.metadata)
        
        try:
            async for response in response_stream:
                result = {}
                
                if response.stdout_chunk:
                    result["stdout"] = response.stdout_chunk.decode('utf-8', errors='replace')
                
                if response.stderr_chunk:
                    result["stderr"] = response.stderr_chunk.decode('utf-8', errors='replace')
                
                if response.finished:
                    result["finished"] = True
                    result["exit_code"] = response.exit_code
                
                if response.error_message:
                    result["error"] = response.error_message
                
                yield result
                
        except grpc.aio.AioRpcError as e:
            logger.error(f"gRPC error in session: {e.code()}: {e.details()}")
            yield {"error": f"gRPC error: {e.details()}"}
        except Exception as e:
            logger.error(f"Error in session: {e}", exc_info=True)
            yield {"error": str(e)}
    
    async def close(self) -> bool:
        """Close session"""
        if not self.is_active:
            return False
        
        request = command_service_pb2.SessionCloseRequest(
            session_id=self.session_id
        )
        
        try:
            response = await self.stub.CloseSession(request, metadata=self.metadata)
            self.is_active = False
            logger.info(f"Session {self.session_id} closed: {response.message}")
            return response.success
        except Exception as e:
            logger.error(f"Error closing session: {e}")
            return False

class TaurusClient:
    # gRPC certificate target name (option 2: matches DNS name in server certificate SAN)
    # Client uses ssl_target_name_override to verify certificate, bypassing IP/hostname matching
    GRPC_TARGET_NAME = "taurus-grpc-server"

    def __init__(
        self, 
        address: str, 
        cert_file: Optional[str] = None, 
        key_file: Optional[str] = None, 
        ca_file: Optional[str] = None, 
        role: str = "operator", 
        target_name: Optional[str] = None,
        secure: bool = True
    ):
        """
        Initialize Taurus client

        Args:
            address: Server address in format "host:port"
            cert_file: Client certificate file path (for mTLS). If not provided and secure=True, will auto-detect
            key_file: Client private key file path (for mTLS). If not provided and secure=True, will auto-detect
            ca_file: CA certificate file path (for server certificate verification). If not provided and secure=True, will auto-detect
            role: Client role, default "operator"
            target_name: gRPC SSL target name override (default "taurus-grpc-server")
            secure: Whether to use secure connection (mTLS), default True. Set to False to use insecure channel
            
        Note:
            If secure=True but no certificate parameters are provided, the SDK will attempt to auto-detect 
            certificate paths in the following order:
            1. Directory specified by TAURUS_CERT_DIR environment variable
            2. tls/ directory under current directory (web_ui.py style: webui-client.crt/key)
            3. certs/sdk/ directory under current directory (SDK style: client.crt/key)
            4. Search upward for tls/ or certs/sdk/ directories in parent directories
        """
        self.address = address
        self.role = role
        self.target_name = target_name or self.GRPC_TARGET_NAME
        self.channel = None
        self.stub = None
        
        # Handle certificate configuration
        # If secure=True but no certificates provided, try auto-detection
        # If certificates still not found, fall back to insecure mode
        if secure:
            if cert_file and key_file and ca_file:
                # Explicitly provided certificates
                self.cert_file = cert_file
                self.key_file = key_file
                self.ca_file = ca_file
                self.secure = True
            else:
                # Try to auto-detect certificates
                auto_cert, auto_key, auto_ca = find_default_certificates()
                if auto_cert and auto_key and auto_ca:
                    self.cert_file = auto_cert
                    self.key_file = auto_key
                    self.ca_file = auto_ca
                    self.secure = True
                    logger.info(f"Auto-detected certificates: {auto_cert}")
                else:
                    # No certificates found, fall back to insecure mode
                    self.cert_file = None
                    self.key_file = None
                    self.ca_file = None
                    self.secure = False
                    logger.warning(
                        "secure=True but no certificates found. "
                        "Falling back to insecure connection. "
                        "Provide certificates explicitly or place them in tls/ or certs/sdk/ directory."
                    )
        else:
            # Explicitly requested insecure mode
            self.cert_file = None
            self.key_file = None
            self.ca_file = None
            self.secure = False

    async def __aenter__(self):
        # Create metadata to pass client role
        metadata = [('x-client-role', self.role)]

        # gRPC channel options
        # Key: add grpc.ssl_target_name_override to skip IP SAN matching validation (option 2)
        options = [
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 10000),
            ('grpc.keepalive_permit_without_calls', 0),
            ('grpc.http2.max_pings_without_data', 1),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),  # 50MB
            ('grpc.max_send_message_length', 50 * 1024 * 1024),  # 50MB
            ('grpc.ssl_target_name_override', self.target_name),
        ]
        
        if self.secure:
            # Use SSL/TLS secure connection
            with open(self.cert_file, 'rb') as f:
                cert_data = f.read()
            with open(self.key_file, 'rb') as f:
                key_data = f.read()
            with open(self.ca_file, 'rb') as f:
                ca_data = f.read()
            
            # Create channel credentials
            credentials = grpc.ssl_channel_credentials(
                root_certificates=ca_data,
                private_key=key_data,
                certificate_chain=cert_data
            )
            
            self.channel = grpc.aio.secure_channel(self.address, credentials, options=options)
            logger.info(f"Using secure connection to {self.address} with role {self.role}")
        else:
            # Use insecure connection
            self.channel = grpc.aio.insecure_channel(self.address, options=options)
            logger.info(f"Using insecure connection to {self.address} with role {self.role}")
            
        self.stub = command_service_pb2_grpc.ClientServiceStub(self.channel)
        self.file_transfer_stub = command_service_pb2_grpc.FileTransferStub(self.channel)
        # Store metadata in instance variable for subsequent calls
        self.metadata = metadata
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.channel:
            await self.channel.close()

    async def execute_command(
        self, command: str, args: Optional[list[str]] = None, timeout: int = 30, environment: Optional[dict] = None, merge_streams: bool = False, shell: bool = False, load_profile: str = "false", working_directory: Optional[str] = None
    ) -> AsyncGenerator[dict, None]:
        """Execute a command and stream back the output.

        Args:
            command: The command to execute
            args: Command arguments (ignored when shell=True, since ``command`` is the full shell line)
            timeout: Execution timeout in seconds
            environment: Environment variables
            merge_streams: If True, stderr is merged into stdout to preserve output order
            shell: If True, run through ``/bin/bash -c`` so that shell operators (&&, ||, |, pipes,
                   quoting, redirections, etc.) are parsed correctly.
            load_profile: Controls bash profile loading when shell=True:
                          "false" (default): ``--noprofile --norc`` — clean environment
                          "true": no flags — loads ``~/.bashrc``
                          "login": ``--login`` — full login shell (``/etc/profile`` + ``~/.bash_profile`` etc.)
            working_directory: Directory to run the command in. Defaults to the server process's CWD.
        """
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.CommandRequest(
            command=command, args=args or [], timeout_seconds=timeout, environment=environment or {}, merge_streams=merge_streams, working_directory=working_directory or ""
        )
        # Propagate the shell flag through the environment so the server-side executor
        # can honor it without a proto schema change.
        if shell:
            request.environment["__TAURUS_USE_SHELL__"] = "true"
        if load_profile and load_profile != "false":
            request.environment["__TAURUS_LOAD_PROFILE__"] = load_profile

        # Make the call
        response_stream = self.stub.ExecuteCommand(request, metadata=self.metadata)
        
        try:
            async for response in response_stream:
                result = {}

                if response.stdout_chunk:
                    result["stdout"] = response.stdout_chunk.decode('utf-8', errors='replace')

                if response.stderr_chunk:
                    result["stderr"] = response.stderr_chunk.decode('utf-8', errors='replace')

                if response.finished:
                    result["finished"] = True
                    result["exit_code"] = response.exit_code

                if response.error_message:
                    result["error"] = response.error_message

                yield result
                
        except asyncio.CancelledError:
            # Explicitly cancel RPC call
            response_stream.cancel()
            # Wait for cancellation to complete
            try:
                async for _ in response_stream:
                    pass  # Consume remaining events until stream is truly closed
            except grpc.aio.AioRpcError:
                pass  # Ignore exceptions that may occur during cancellation
            raise
        except grpc.aio.AioRpcError as e:
            logger.error(f"gRPC error during command execution: {e.code()}: {e.details()}")
            yield {"error": f"gRPC error: {e.details()}"}
        except Exception as e:
            logger.error(f"Unexpected error during command execution: {e}", exc_info=True)
            yield {"error": str(e)}

    async def get_status(self) -> dict:
        """Get the client's status."""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")
    
        request = command_service_pb2.StatusRequest()
        response = await self.stub.GetStatus(request, metadata=self.metadata)
        return {
            "version": response.version,
            "uptime": response.uptime,
            "hostname": response.hostname,
            "cpu_usage": response.cpu_usage,
            "memory_usage": response.memory_usage,
        }

    async def send_signal(self, pid: int, signal_num: int) -> dict:
        """Send a signal to a running process by PID.

        Args:
            pid: Process ID (must be a process group leader)
            signal_num: Signal number (19=SIGSTOP, 18=SIGCONT, 9=SIGKILL)
        """
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.SendSignalRequest(pid=pid, signal=signal_num)
        response = await self.stub.SendSignal(request, metadata=self.metadata)
        return {
            "success": response.success,
            "message": response.message,
        }

    async def list_executions(self) -> list[dict]:
        """List all currently executing commands."""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.ListExecutionsRequest()
        response = await self.stub.ListExecutions(request, metadata=self.metadata)
        return [
            {
                "execution_id": e.execution_id,
                "command": e.command,
                "args": list(e.args),
                "pid": e.pid,
                "status": e.status,
                "started_at": e.started_at,
            }
            for e in response.executions
        ]

    async def upload_file(self, local_path: str, remote_path: str, chunk_size: int = 64 * 1024) -> dict:
        """Upload a file to the client.

        Args:
            local_path: Local file path to upload
            remote_path: Remote file path on the client
            chunk_size: Size of each chunk in bytes (default 64KB)
        """
        if not self.file_transfer_stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        total_size = os.path.getsize(local_path)
        file_name = os.path.basename(local_path)

        async def generate_chunks():
            with open(local_path, "rb") as f:
                chunk_index = 0
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    is_last = (f.tell() >= total_size)
                    yield command_service_pb2.UploadRequest(
                        file_path=remote_path,
                        chunk=chunk,
                        is_last=is_last,
                        total_size=total_size,
                        chunk_index=chunk_index,
                    )
                    chunk_index += 1

        response = await self.file_transfer_stub.UploadFile(generate_chunks(), metadata=self.metadata)
        return {
            "success": response.success,
            "message": response.message,
            "bytes_received": response.bytes_received,
        }

    async def download_file(self, remote_path: str, local_path: str, chunk_size: int = 64 * 1024) -> dict:
        """Download a file from the client.

        Args:
            remote_path: Remote file path on the client
            local_path: Local file path to save to
            chunk_size: Size of each chunk in bytes (default 64KB)
        """
        if not self.file_transfer_stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.DownloadRequest(
            file_path=remote_path,
            offset=0,
            chunk_size=chunk_size,
        )

        response_stream = self.file_transfer_stub.DownloadFile(request, metadata=self.metadata)

        total_size = 0
        bytes_received = 0

        # Ensure parent directory exists
        parent_dir = os.path.dirname(local_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        with open(local_path, "wb") as f:
            async for response in response_stream:
                if response.chunk:
                    f.write(response.chunk)
                    bytes_received += len(response.chunk)
                    total_size = response.total_size

        return {
            "success": True,
            "message": f"File downloaded successfully: {local_path}",
            "bytes_received": bytes_received,
            "total_size": total_size,
        }

    async def list_directory(self, path: str) -> list[dict]:
        """List directory contents on the client.

        Args:
            path: Remote directory path
        """
        if not self.file_transfer_stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.ListDirectoryRequest(path=path)
        response = await self.file_transfer_stub.ListDirectory(request, metadata=self.metadata)

        if response.error:
            raise Exception(response.error)

        return [
            {
                "name": e.name,
                "size": e.size,
                "is_dir": e.is_dir,
                "modified_at": e.modified_at,
                "permissions": e.permissions,
                "owner": e.owner,
                "group": e.group,
            }
            for e in response.entries
        ]
    
    async def cancel_current_command(self) -> None:
        """
        Cancel the currently executing command.
        Note: This is a placeholder method. In reality, you would need to keep
        track of active calls and cancel them individually.
        """
        # This method would typically hold references to active RPC calls
        # and cancel them when needed. For now, we just log a message.
        logger.info("Cancel command functionality would be implemented here.")

    async def create_session(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        working_directory: Optional[str] = None,
        environment: Optional[dict] = None,
        shell: str = "/bin/bash",
        timeout: int = 3600,
    ) -> Session:
        """Create interactive session"""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")
        
        request = command_service_pb2.SessionRequest(
            username=username or "",
            password=password or "",
            working_directory=working_directory or "",
            environment=environment or {},
            timeout_seconds=timeout,
            shell=shell,
        )
        
        response = await self.stub.CreateSession(request, metadata=self.metadata)
        
        if not response.success:
            raise RuntimeError(f"Failed to create session: {response.message}")
        
        logger.info(f"Session created: {response.session_id}")
        return Session(response.session_id, self.stub, self.metadata)

    async def list_sessions(self) -> list:
        """List all active sessions"""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")
        
        request = command_service_pb2.ListSessionsRequest()
        response = await self.stub.ListSessions(request, metadata=self.metadata)
        
        return [
            {
                "session_id": s.session_id,
                "username": s.username,
                "working_directory": s.working_directory,
                "created_at": s.created_at,
                "last_active": s.last_active,
                "is_alive": s.is_alive,
            }
            for s in response.sessions
        ]