"""File transfer gRPC service handlers."""

import grp
import logging
import os
import pwd
import stat
from collections.abc import AsyncGenerator
from typing import Any

import grpc

from .generated.executor.v1 import command_service_pb2
from .generated.executor.v1.command_service_pb2_grpc import FileTransferServicer
from ..infra.config import settings
from ..infra.logger import add_context_to_log_record

logger = logging.getLogger(__name__)

# Default chunk size: 64 KB
DEFAULT_CHUNK_SIZE = 64 * 1024


def _parse_peer_ip(peer: str) -> str:
    """Extract IP from gRPC peer string."""
    if peer.startswith("ipv4:"):
        return peer[5:].split(":")[0]
    elif peer.startswith("ipv6:"):
        raw = peer[5:]
        # URL-encoded brackets: %5B = [, %5D = ]
        raw = raw.replace("%5B", "").replace("%5D", "")
        # IPv4-mapped IPv6: ::ffff:1.2.3.4 -> 1.2.3.4
        if raw.startswith("::ffff:"):
            return raw[7:]
        return raw
    return peer


def _format_permissions(mode: int) -> str:
    """Convert file mode to permission string like 'drwxr-xr-x'."""
    if stat.S_ISDIR(mode):
        result = "d"
    elif stat.S_ISLNK(mode):
        result = "l"
    else:
        result = "-"

    for who in ("USR", "GRP", "OTH"):
        for what in ("R", "W", "X"):
            bit = getattr(stat, f"S_I{what}{who}")
            result += "x" if mode & bit else "-"
    return result


class FileTransferServicerImpl(FileTransferServicer):
    """Handles file upload, download, and directory listing."""

    async def UploadFile(
        self,
        request_iterator: AsyncGenerator[command_service_pb2.UploadRequest, None],
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.UploadResponse:
        """Upload a file from client to client (client streaming)."""
        client_ip = _parse_peer_ip(context.peer())
        add_context_to_log_record({"client_ip": client_ip})

        file_path = None
        total_size = 0
        bytes_received = 0
        file_obj = None
        file_existed = False

        try:
            async for request in request_iterator:
                if file_path is None:
                    file_path = request.file_path
                    total_size = request.total_size

                    # Validate path
                    if not file_path:
                        return command_service_pb2.UploadResponse(
                            success=False, message="file_path is required"
                        )

                    if settings.allow_file_outside_home:
                        candidate = os.path.realpath(file_path)
                        if not candidate or candidate in (os.sep, ''):
                            logger.warning(f"Upload path rejected (invalid): {file_path}")
                            return command_service_pb2.UploadResponse(
                                success=False,
                                message="file_path is invalid",
                            )
                        file_path = candidate
                    else:
                        upload_root = os.path.realpath(os.path.expanduser("~"))
                        if os.path.isabs(file_path):
                            candidate = os.path.realpath(file_path)
                        else:
                            candidate = os.path.realpath(os.path.join(upload_root, file_path))
                        if candidate != upload_root and not candidate.startswith(upload_root + os.sep):
                            logger.warning(f"Upload path rejected (outside upload root): {file_path}")
                            return command_service_pb2.UploadResponse(
                                success=False,
                                message="file_path must stay within the user home directory",
                            )
                        file_path = candidate

                    # Ensure parent directory exists
                    parent_dir = os.path.dirname(file_path)
                    if parent_dir:
                        os.makedirs(parent_dir, exist_ok=True)

                    file_existed = os.path.exists(file_path)
                    file_obj = open(file_path, "wb")
                    logger.info(
                        f"Upload started: {file_path} (total_size={total_size})"
                    )

                # Write chunk
                if request.chunk:
                    file_obj.write(request.chunk)
                    bytes_received += len(request.chunk)


                if request.is_last:
                    break

            if file_obj:
                file_obj.close()

            if file_path:
                logger.info(f"Upload completed: {file_path} ({bytes_received} bytes)")

            return command_service_pb2.UploadResponse(
                success=True,
                message=f"File uploaded successfully: {file_path}",
                bytes_received=bytes_received,
            )

        except Exception as e:
            if file_obj:
                file_obj.close()
            # Clean up partial file — only if this upload created it
            # (never delete a pre-existing file that we merely truncated)
            if file_path and not file_existed and os.path.exists(file_path):
                os.remove(file_path)
            logger.error(f"Upload failed: {e}", exc_info=True)
            return command_service_pb2.UploadResponse(
                success=False, message=f"Upload failed: {e}"
            )

    async def DownloadFile(
        self,
        request: command_service_pb2.DownloadRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> AsyncGenerator[command_service_pb2.DownloadResponse, None]:
        """Download a file from client to client (server streaming)."""
        client_ip = _parse_peer_ip(context.peer())
        add_context_to_log_record({"client_ip": client_ip})

        file_path = request.file_path
        offset = request.offset or 0
        chunk_size = request.chunk_size or DEFAULT_CHUNK_SIZE

        if not file_path:
            yield command_service_pb2.DownloadResponse(
                chunk=b"", offset=0, is_last=True, total_size=0,
                file_name="",
            )
            return

        # Resolve relative paths against home directory
        if not os.path.isabs(file_path):
            home_dir = os.path.expanduser("~")
            file_path = os.path.join(home_dir, file_path)

        if not os.path.isfile(file_path):
            logger.warning(f"Download file not found: {file_path}")
            yield command_service_pb2.DownloadResponse(
                chunk=b"", offset=0, is_last=True, total_size=0,
                file_name=os.path.basename(file_path),
            )
            return

        total_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        logger.info(f"Download started: {file_path} (offset={offset}, size={total_size})")

        try:
            with open(file_path, "rb") as f:
                f.seek(offset)
                current_offset = offset

                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    is_last = (current_offset + len(chunk)) >= total_size
                    yield command_service_pb2.DownloadResponse(
                        chunk=chunk,
                        offset=current_offset,
                        is_last=is_last,
                        total_size=total_size,
                        file_name=file_name,
                    )
                    current_offset += len(chunk)

        except Exception as e:
            logger.error(f"Download failed: {e}", exc_info=True)

    async def ListDirectory(
        self,
        request: command_service_pb2.ListDirectoryRequest,
        context: "grpc.aio.ServicerContext[Any, Any]",
    ) -> command_service_pb2.ListDirectoryResponse:
        """List directory contents."""
        client_ip = _parse_peer_ip(context.peer())
        add_context_to_log_record({"client_ip": client_ip})

        path = request.path or "."

        if not os.path.isdir(path):
            return command_service_pb2.ListDirectoryResponse(
                path=path,
                entries=[],
                error=f"Not a directory: {path}",
            )

        try:
            entries = []
            for name in sorted(os.listdir(path)):
                full_path = os.path.join(path, name)
                try:
                    st = os.lstat(full_path)
                    owner = ""
                    group = ""
                    try:
                        owner = pwd.getpwuid(st.st_uid).pw_name
                    except KeyError:
                        owner = str(st.st_uid)
                    try:
                        group = grp.getgrgid(st.st_gid).gr_name
                    except KeyError:
                        group = str(st.st_gid)

                    entries.append(command_service_pb2.DirEntry(
                        name=name,
                        size=st.st_size,
                        is_dir=stat.S_ISDIR(st.st_mode),
                        modified_at=int(st.st_mtime),
                        permissions=_format_permissions(st.st_mode),
                        owner=owner,
                        group=group,
                    ))
                except OSError:
                    entries.append(command_service_pb2.DirEntry(
                        name=name,
                        size=0,
                        is_dir=False,
                        modified_at=0,
                        permissions="----------",
                        owner="",
                        group="",
                    ))

            logger.info(f"ListDirectory: {path} ({len(entries)} entries)")
            return command_service_pb2.ListDirectoryResponse(
                path=path,
                entries=entries,
            )

        except Exception as e:
            logger.error(f"ListDirectory failed: {e}", exc_info=True)
            return command_service_pb2.ListDirectoryResponse(
                path=path,
                entries=[],
                error=str(e),
            )
