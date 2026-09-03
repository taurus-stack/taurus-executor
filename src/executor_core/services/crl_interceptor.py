"""
CRL (Certificate Revocation List) interceptor
Used to check if client certificate has been revoked during gRPC connection
"""
import asyncio
import grpc
import logging
import os
import re
import time
from typing import Optional, Tuple
from pathlib import Path


logger = logging.getLogger(__name__)


# Methods whose response is server-streaming (must use unary_stream terminator)
_STREAMING_METHOD_SUFFIXES = (
    "/ExecuteCommand",
    "/ExecuteInSession",
    "/DownloadFile",
    "/ListDirectory",
)


class CRLInterceptor(grpc.aio.ServerInterceptor):
    """
    CRL interceptor
    Checks if client certificate serial number is in the revocation list.

    NOTE: Openssl CRL text output is cached for ``cache_ttl`` seconds to avoid
    repeatedly spawning ``openssl`` subprocesses on every RPC.  The openssl
    invocation itself runs through ``asyncio.create_subprocess_exec`` so it
    never blocks the asyncio event loop.
    """

    def __init__(self, crl_path: Optional[str] = None, cache_ttl: int = 300):
        """
        Initialize CRL interceptor

        Args:
            crl_path: CRL file path, if not provided will get from environment variable
            cache_ttl: How long (seconds) to cache the parsed CRL output.  Default 5 min.
        """
        if crl_path:
            self.crl_path = Path(crl_path)
        else:
            tls_dir = os.getenv('TLS_DIR')
            if tls_dir:
                self.crl_path = Path(tls_dir) / 'crl.pem'
            else:
                self.crl_path = Path.home() / '.taurus-executor' / 'tls' / 'crl.pem'

        self._cache_ttl = max(1, int(cache_ttl))
        self._cached_text: Optional[str] = None
        self._cached_mtime: Optional[float] = None
        self._cached_at: float = 0.0
        self._parse_lock: Optional[asyncio.Lock] = None

    @property
    def _lock(self) -> asyncio.Lock:
        if self._parse_lock is None:
            self._parse_lock = asyncio.Lock()
        return self._parse_lock

    async def _load_crl_text(self) -> Optional[str]:
        """Load CRL text via async subprocess, with in-memory + mtime caching."""
        if not self.crl_path.exists():
            self._cached_text = None
            self._cached_mtime = None
            return None

        try:
            stat = self.crl_path.stat()
            mtime = stat.st_mtime
        except OSError as e:
            logger.warning("CRL stat failed: %s", e)
            return None

        now = time.monotonic()
        if (
            self._cached_text is not None
            and self._cached_mtime == mtime
            and (now - self._cached_at) < self._cache_ttl
        ):
            return self._cached_text

        async with self._lock:
            now = time.monotonic()
            if (
                self._cached_text is not None
                and self._cached_mtime == mtime
                and (now - self._cached_at) < self._cache_ttl
            ):
                return self._cached_text

            try:
                proc = await asyncio.create_subprocess_exec(
                    'openssl', 'crl',
                    '-in', str(self.crl_path),
                    '-text',
                    '-noout',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=5
                    )
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    logger.error("CRL parse timeout (openssl hung)")
                    return None

                if proc.returncode != 0:
                    logger.error(
                        "CRL parse failed: rc=%s stderr=%s",
                        proc.returncode,
                        stderr_bytes.decode('utf-8', errors='replace')[:500],
                    )
                    return None

                text = stdout_bytes.decode('utf-8', errors='replace')
            except FileNotFoundError:
                logger.warning("openssl binary not found, skipping CRL check")
                return None
            except Exception as e:
                logger.error("CRL parse exception: %s", e)
                return None

            self._cached_text = text
            self._cached_mtime = mtime
            self._cached_at = time.monotonic()
            return text

    # Matches revoked serial entries in `openssl crl -text` output,
    # e.g. "        Serial Number: 4A3B..."
    _SERIAL_NUMBER_RE = re.compile(r'Serial Number:\s*([0-9A-Fa-f]+)')

    @staticmethod
    def _serial_in_crl(crl_text: str, certificate_serial: str) -> bool:
        """Exact-match comparison of the certificate serial against revoked
        serial entries parsed from the CRL text (avoids substring false positives)."""
        try:
            cert_serial_int = int(certificate_serial.strip(), 16)
        except (ValueError, TypeError):
            return False

        for match in CRLInterceptor._SERIAL_NUMBER_RE.finditer(crl_text):
            try:
                if int(match.group(1), 16) == cert_serial_int:
                    return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _create_terminator(code, details, handler_call_details):
        """Create a terminator matching the RPC method's stream type."""
        def terminate(ignored_request, context):
            context.abort(code, details)

        method = getattr(handler_call_details, 'method', '') or ''
        if any(method.endswith(suf) for suf in _STREAMING_METHOD_SUFFIXES):
            return grpc.unary_stream_rpc_method_handler(terminate)
        return grpc.unary_unary_rpc_method_handler(terminate)

    async def intercept_service(self, continuation, handler_call_details):
        """Intercept gRPC request, check certificate revocation status."""
        if not self.crl_path.exists():
            logger.debug("CRL file not found, skipping revocation check")
            return await continuation(handler_call_details)

        try:
            metadata = dict(handler_call_details.invocation_metadata)
            cert_serial = metadata.get('x-cert-serial')

            if cert_serial:
                crl_text = await self._load_crl_text()
                if crl_text is not None and self._serial_in_crl(crl_text, cert_serial):
                    logger.warning(
                        "Certificate revoked: serial=%s, method=%s",
                        cert_serial,
                        handler_call_details.method,
                    )
                    return self._create_terminator(
                        grpc.StatusCode.PERMISSION_DENIED,
                        f"Certificate has been revoked: {cert_serial}",
                        handler_call_details,
                    )
                logger.debug("Certificate valid: serial=%s", cert_serial)
        except Exception as e:
            logger.error("CRL check failed: %s, allowing request to proceed", e)

        return await continuation(handler_call_details)