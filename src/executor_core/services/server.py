import asyncio
import logging
import signal
import sys
import os
from pathlib import Path

import grpc

# Subprocess creation uses ``os.posix_spawn`` (not fork), so gRPC fork
# support is unnecessary and disabled.  No pthread_atfork handlers are
# triggered by our code path.
grpc.enable_fork_support = False

from executor_core.services.auth_interceptor import AuthInterceptor
from prometheus_client import start_http_server
from executor_core.services.crl_interceptor import CRLInterceptor
from executor_core.services.ticket_auth_interceptor import TicketAuthInterceptor

from .generated.executor.v1.command_service_pb2_grpc import add_ClientServiceServicer_to_server
from .generated.executor.v1.command_service_pb2_grpc import add_FileTransferServicer_to_server
from .handlers import ClientServicer
from .file_transfer_handler import FileTransferServicerImpl
from ..infra.config import settings
from ..infra.logger import setup_logging
from ..infra.state_manager import StateManager

logger = logging.getLogger(__name__)


# ── Global state: for inter-coroutine communication ──────────────────────────────────────────────────
# "Needs restart" flag set by certificate renewal task
_certificate_needs_restart = False
# "Needs restart" flag set by external signals
_signal_needs_restart = False
# Shutdown flag
_needs_shutdown = False


def _load_tls_credentials():
    """
    Load TLS credentials from disk files

    Returns:
        (bool, grpc.ServerCredentials) tuple:
            use_tls: Whether to use TLS
            server_credentials: gRPC server credentials (None means no TLS)
    """
    cert_path, key_path, ca_path = settings.get_server_tls_files()
    tls_files = [key_path, cert_path, ca_path]
    missing_files = []

    for f in tls_files:
        if not f.exists():
            missing_files.append(str(f))

    # Retry once (wait for disk IO)
    if missing_files:
        import time
        time.sleep(0.1)
        missing_files = [
            str(f) for f in tls_files if not f.exists()
        ]

    if missing_files:
        logger.warning(
            "Missing or unreadable TLS files: %s. Starting server without TLS.",
            missing_files
        )
        return False, None

    try:
        # Re-read from disk on each call to ensure latest certificate files are used
        with open(key_path, "rb") as f:
            private_key = f.read()
        with open(cert_path, "rb") as f:
            certificate_chain = f.read()
        with open(ca_path, "rb") as f:
            ca_cert = f.read()

        server_credentials = grpc.ssl_server_credentials(
            [(private_key, certificate_chain)],
            root_certificates=ca_cert,
            require_client_auth=True,
        )
        logger.info("TLS credentials loaded from %s", cert_path.parent)
        return True, server_credentials

    except Exception as e:
        logger.error("Failed to load TLS credentials: %s. Starting without TLS.", e)
        return False, None


def _build_interceptors():
    """Build gRPC interceptor list"""
    interceptors = [CRLInterceptor(), AuthInterceptor()]
    if settings.ticket_auth_enabled:
        interceptors.append(TicketAuthInterceptor())
        logger.info("Ticket authentication enabled")
    return interceptors


async def _serve_one_round():
    """
    Run one round of gRPC server (until stop/restart signal is received)

    Returns:
        str: 'shutdown' means stop, 'restart' means restart
    """
    global _certificate_needs_restart, _signal_needs_restart, _needs_shutdown

    # Reload certificates from disk on each restart (ensure new certificates take effect)
    use_tls, server_credentials = _load_tls_credentials()

    # Create gRPC server
    interceptors = _build_interceptors()
    server = grpc.aio.server(interceptors=interceptors)

    # State manager
    state_manager = StateManager()

    # Register servicer
    servicer = ClientServicer(state_manager)
    add_ClientServiceServicer_to_server(servicer, server)
    file_transfer_servicer = FileTransferServicerImpl()
    add_FileTransferServicer_to_server(file_transfer_servicer, server)

    # Start session manager
    await servicer.session_manager.start()
    logger.info("Session manager started")

    # Bind port
    listen_addr = f"{settings.grpc_host}:{settings.grpc_port}"
    if use_tls and server_credentials:
        server.add_secure_port(listen_addr, server_credentials)
        logger.info("Using secure connection (TLS) on %s", listen_addr)
    else:
        server.add_insecure_port(listen_addr)
        logger.warning("Using insecure connection (no TLS) on %s", listen_addr)

    # Start
    await server.start()
    logger.info("Server listening on %s", listen_addr)

    # Wait for exit/restart conditions
    check_event = asyncio.Event()

    # Unified signal handling via the event loop (single mechanism; the outer
    # loop does NOT install its own signal.signal handlers)
    def _handle_sigusr1():
        global _signal_needs_restart
        logger.info("Received SIGUSR1, will restart server...")
        _signal_needs_restart = True
        check_event.set()

    def _handle_shutdown_signal(sig):
        global _needs_shutdown
        logger.info("Received signal %d, initiating graceful shutdown...", sig)
        _needs_shutdown = True
        check_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, _handle_sigusr1)
    loop.add_signal_handler(signal.SIGINT, _handle_shutdown_signal, signal.SIGINT)
    loop.add_signal_handler(signal.SIGTERM, _handle_shutdown_signal, signal.SIGTERM)

    # Periodically check global flag (set by certificate renewal task)
    async def _poll_certificate_status():
        global _certificate_needs_restart, _needs_shutdown
        try:
            while not _needs_shutdown:
                await asyncio.sleep(60)  # Check every 60 seconds
                if _certificate_needs_restart:
                    logger.info("Detected certificate renewal flag, will restart server...")
                    check_event.set()
                    break
        except asyncio.CancelledError:
            pass

    poll_task = asyncio.create_task(_poll_certificate_status())

    # Wait for exit condition
    await check_event.wait()
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

    # Stop session manager
    await servicer.session_manager.stop()
    logger.info("Session manager stopped")

    # Gracefully shutdown gRPC server
    logger.info("Shutting down server...")
    await server.stop(grace=5)
    logger.info("Server shutdown complete")

    # Close interceptors that hold resources (e.g. TicketAuthInterceptor's
    # aiohttp ClientSession) to avoid leaking sessions on each restart round
    for interceptor in interceptors:
        close = getattr(interceptor, 'close', None)
        if close is not None:
            try:
                await close()
            except Exception as e:
                logger.warning("Failed to close interceptor %s: %s", type(interceptor).__name__, e)

    # Determine next action
    if _needs_shutdown:
        return 'shutdown'
    # Any other case (SIGUSR1 or certificate renewal flag) means restart
    if _signal_needs_restart or _certificate_needs_restart:
        return 'restart'
    return 'shutdown'


async def serve_with_renewal_tasks(
    days_before_renew: int = 30,
    check_interval_hours: int = 24,
) -> None:
    """
    Main service entry: start gRPC server + background certificate renewal task

    Flow:
    1. Start prometheus metrics server based on METRICS_ENABLED configuration (disabled by default)
    2. Start background certificate renewal coroutine (check every check_interval_hours hours)
    3. Run gRPC server main loop
    4. When certificate renewal succeeds or SIGUSR1 signal is received, restart gRPC server (reload certificates)
    5. Graceful exit on SIGINT/SIGTERM

    Args:
        days_before_renew: How many days in advance to start certificate renewal
        check_interval_hours: Certificate check interval (hours)
    """
    global _certificate_needs_restart, _signal_needs_restart, _needs_shutdown

    # Setup logging
    log_dir = str(settings.get_log_dir())
    setup_logging(
        level=settings.log_level,
        format_type=settings.log_format,
        log_dir=log_dir,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )

    logger.info("Starting Taurus Executor server...")

    # Signal handlers (SIGINT/SIGTERM/SIGUSR1) are registered inside
    # _serve_one_round via loop.add_signal_handler — a single unified mechanism

    # Metrics server (configurable, disabled by default, unified process-level monitoring by Supervisor)
    if settings.metrics_enabled:
        start_http_server(settings.metrics_port)
        logger.info("Metrics server started on port %d", settings.metrics_port)
    else:
        logger.info("Metrics server disabled (METRICS_ENABLED=false)")

    # Start background certificate renewal coroutine
    renewal_task = asyncio.create_task(
        _certificate_renewal_coroutine(
            days_before_renew=days_before_renew,
            check_interval_hours=check_interval_hours,
        )
    )

    try:
        # Main loop: server start -> restart -> start again
        while True:
            # Clear restart flags, enter new round
            _certificate_needs_restart = False
            _signal_needs_restart = False

            result = await _serve_one_round()

            if result == 'restart':
                logger.info("=== Restarting gRPC server (reloading certificates) ===")
                # Brief wait to allow async cleanup
                await asyncio.sleep(1)
                continue
            else:
                # shutdown
                logger.info("Server shutdown requested, exiting main loop")
                break

    except asyncio.CancelledError:
        logger.info("Server loop cancelled (shutdown)")
    finally:
        renewal_task.cancel()
        try:
            await renewal_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("Taurus Executor server fully stopped")


async def _certificate_renewal_coroutine(
    days_before_renew: int = 30,
    check_interval_hours: int = 24,
) -> None:
    """
    Background certificate renewal coroutine

    Note: Does not restart server directly, only sets global flag `_certificate_needs_restart = True`,
    the server coroutine will poll and restart itself (this ensures thread/coroutine safety).
    """
    global _certificate_needs_restart

    from executor_core.infra.tls_manager import TLSCertificateManager
    from executor_core.infra.cert_client import get_certificate_client

    check_interval_seconds = check_interval_hours * 3600

    logger.info(
        "Background certificate renewal task started: "
        "check every %d hours, renew %d days before expiry",
        check_interval_hours, days_before_renew
    )

    # Initial delay to let server finish starting up
    await asyncio.sleep(5)

    while True:
        try:
            logger.info("=== Periodic certificate check ===")
            tls_dir = settings.get_tls_dir()
            manager = TLSCertificateManager(tls_dir)

            # Check certificate status
            days_left = manager.check_server_cert_expiry(days_before_renew)
            needs_renewal = (
                days_left is None
                or days_left <= days_before_renew
            )

            if not needs_renewal:
                logger.info(
                    "Server certificate valid for %d more days, no renewal needed",
                    days_left
                )
                # Wait for next check
                await asyncio.sleep(check_interval_seconds)
                continue

            # Needs renewal: read configuration from environment variables/state.json
            import json as _json
            server_url = None
            host_uuid = None

            state_paths = [
                Path(str(Path.home())) / '.taurus' / 'data' / 'state.json',
                Path('/opt/taurus/data/state.json'),
                Path(str(Path.home())) / 'taurus' / 'data' / 'state.json',
            ]
            for sf in state_paths:
                if sf.exists():
                    try:
                        with open(sf, 'r') as f:
                            s = _json.load(f)
                        if s.get('server_url') and s.get('host_id'):
                            server_url = s['server_url']
                            host_uuid = str(s['host_id'])
                            break
                    except Exception as e:
                        logger.warning("Failed to read state file %s: %s", sf, e)

            server_url = server_url or os.environ.get("TAURUS_SERVER_URL")
            host_uuid = host_uuid or os.environ.get("TAURUS_HOST_UUID")

            if not server_url or not host_uuid:
                logger.warning(
                    "Cannot renew certificate: server_url or host_uuid not configured. "
                    "Set environment variables TAURUS_SERVER_URL / TAURUS_HOST_UUID, "
                    "or ensure Supervisor has registered the host."
                )
                await asyncio.sleep(check_interval_seconds)
                continue

            cert_client = get_certificate_client(
                tls_dir=tls_dir, server_url=server_url, host_uuid=host_uuid
            )
            if not cert_client:
                logger.warning("Failed to create certificate client")
                await asyncio.sleep(check_interval_seconds)
                continue

            # Request renewal
            success = await cert_client.ensure_server_certificate(
                days_before_renew=days_before_renew
            )

            if success:
                new_days_left = manager.check_server_cert_expiry(days_before_renew)
                if new_days_left is not None and new_days_left > days_before_renew:
                    logger.info(
                        "Certificate renewal successful! New certificate valid "
                        "for %d days. Will restart gRPC server to reload certificates.",
                        new_days_left
                    )
                    _certificate_needs_restart = True
                else:
                    # ensure_server_certificate returned true but certificate still expired / expiring soon
                    # possibly because server only returned the same data as the old certificate
                    logger.info(
                        "Certificate renewed, but remaining days (%s) still low. "
                        "Will retry on next check cycle.",
                        new_days_left
                    )
            else:
                logger.warning("Certificate renewal failed, will retry later")

        except asyncio.CancelledError:
            logger.info("Certificate renewal task cancelled")
            break
        except Exception as e:
            logger.error("Certificate renewal task error: %s", e, exc_info=True)

        # Wait for next round
        try:
            await asyncio.sleep(check_interval_seconds)
        except asyncio.CancelledError:
            break

    logger.info("Certificate renewal task exited")


# ── Legacy entry point (kept for external scripts/tests) ────────────────────────────────
async def serve() -> None:
    """
    Legacy entry: equivalent to serve_with_renewal_tasks(), for backward compatibility
    """
    await serve_with_renewal_tasks()


def main() -> None:
    """Entry point for the executor service."""
    try:
        asyncio.run(serve_with_renewal_tasks())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except SystemExit as e:
        if e.code == 75:
            logger.info("Executor exiting for restart")
            sys.exit(75)
        else:
            logger.error(f"Fatal error with exit code {e.code}: {e}")
            sys.exit(e.code)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()