
#!/usr/bin/env python3
"""
Taurus Executor - Main Entry Point
"""
import sys
import os
import asyncio
import signal

# Before any initialization: determine the real user's HOME via uid (do not rely on the HOME
# environment variable which may be overridden externally), then switch the process working
# directory and synchronize HOME/USER/LOGNAME/SHELL. This ensures consistent behavior between
# supervisor-launched and manually-launched processes, and avoids "inconsistent pwd directory" issues.
try:
    import pwd as _pwd
    _pw = _pwd.getpwuid(os.getuid())
    _home = _pw.pw_dir
    # Also synchronize environment variables so subsequent code (e.g. os.path.expanduser("~")) behaves consistently
    os.environ['HOME'] = _home
    os.environ.setdefault('USER', _pw.pw_name)
    os.environ.setdefault('LOGNAME', _pw.pw_name)
    if _pw.pw_shell:
        os.environ.setdefault('SHELL', _pw.pw_shell)
    if os.path.isdir(_home) and os.getcwd() != _home:
        os.chdir(_home)
except Exception:
    pass

# All subprocess creation goes through ``os.posix_spawn`` which does NOT
# invoke fork(2) or the pthread_atfork handlers registered by gRPC.
# Explicitly disable gRPC fork support since it is unnecessary overhead
# when fork is never called.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
# Also ensure gRPC does not install fork handlers at all.
os.environ.setdefault("GRPC_POLL_STRATEGY", "epoll1")

# Adapt paths for PyInstaller packaged binary
if getattr(sys, 'frozen', False):
    # Running as packaged binary
    # sys._MEIPASS is the PyInstaller extraction temp directory
    # We need to ensure modules can be imported correctly
    # PyInstaller has already bundled all dependencies, no extra path setup needed
    pass
else:
    # Running in development environment
    # Add the src directory to the path so we can import our modules
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from executor_core.infra.config import settings
from executor_core.infra.tls_manager import TLSCertificateManager
from executor_core.infra.logger import get_logger, setup_logging

# Initialize logging system early so certificate-related logs can be output
from pathlib import Path
log_dir = str(settings.get_log_dir())
setup_logging(
    level=settings.log_level,
    format_type=settings.log_format,
    log_dir=log_dir,
    max_bytes=settings.log_max_bytes,
    backup_count=settings.log_backup_count,
)

logger = get_logger(__name__)

# Global flag: gRPC server needs restart after certificate renewal succeeds
_certificate_renewed_needs_restart = False


def load_config_from_supervisor() -> tuple:
    """
    Load configuration from Supervisor state file

    Returns:
        (server_url, host_uuid) tuple, or (None, None) if unable to load
    """
    import json

    # Try common Supervisor state file paths
    state_paths = [
        Path.home() / '.taurus' / 'data' / 'state.json',  # Regular user
        Path('/opt/taurus/data/state.json'),  # Root user
        Path.home() / 'taurus' / 'data' / 'state.json',  # Legacy regular user
    ]

    for state_file in state_paths:
        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)

                server_url = state.get('server_url')
                host_id = state.get('host_id')

                if server_url and host_id:
                    logger.info(f"Loaded configuration from Supervisor state file: {state_file}")
                    logger.info(f"  Server URL: {server_url}")
                    logger.info(f"  Host ID: {host_id}")
                    return server_url, str(host_id)

            except Exception as e:
                logger.warning(f"Failed to read Supervisor state file {state_file}: {e}")

    return None, None


async def request_server_certificate_from_server(tls_manager, days_before_renew: int = 30) -> bool:
    """
    Attempt to get server certificate from server (for gRPC server)

    First checks if certificate needs renewal, only requests if necessary.

    Args:
        tls_manager: TLS certificate manager instance
        days_before_renew: How many days in advance to start renewal

    Returns:
        Whether successfully obtained or already has a valid certificate
    """
    from executor_core.infra.cert_client import get_certificate_client

    # Prefer to get configuration from Supervisor state file
    server_url, host_uuid = load_config_from_supervisor()

    # If Supervisor state file has no configuration, try environment variables
    if not server_url or not host_uuid:
        server_url = server_url or os.getenv("TAURUS_SERVER_URL")
        host_uuid = host_uuid or os.getenv("TAURUS_HOST_UUID")

    if not server_url or not host_uuid:
        logger.info("Server URL or Host UUID not configured, skipping server certificate request")
        logger.info("Please ensure Supervisor is registered and state.json file is generated")
        logger.info("Or set environment variables: TAURUS_SERVER_URL, TAURUS_HOST_UUID")
        return False

    logger.info("Server certificate configuration: server_url=%s, host_uuid=%s", server_url, host_uuid)

    cert_client = get_certificate_client(
        tls_dir=tls_manager.tls_dir,
        server_url=server_url,
        host_uuid=host_uuid,
    )

    if not cert_client:
        logger.info("Failed to initialize certificate client")
        return False

    # Use ensure_server_certificate() - check expiration first before deciding to request
    return await cert_client.ensure_server_certificate(days_before_renew=days_before_renew)


async def ensure_tls_certificates(days_before_renew: int = 30) -> bool:
    """
    Ensure TLS server certificate exists and is valid

    Flow:
    1. Check if local server certificate exists and is not expired
    2. If not exists or expiring soon (remaining ≤ days_before_renew days), request from server
    3. If server request fails, start in non-TLS mode (log error)

    Args:
        days_before_renew: How many days in advance to start renewal

    Returns:
        Whether a valid server certificate exists
    """
    tls_dir = settings.get_tls_dir()
    manager = TLSCertificateManager(tls_dir)

    logger.info("Checking server certificate validity (renew %d days in advance)...", days_before_renew)

    # 1. Check local server certificate (includes expiration check)
    days_left = manager.check_server_cert_expiry(days_before_renew)

    if days_left is not None and days_left > days_before_renew:
        logger.info("Local server certificate is valid, %d days remaining, no renewal needed", days_left)
        return True

    # 2. Certificate does not exist / expired / expiring soon, request from server
    if days_left is None:
        logger.info("Local server certificate does not exist, requesting...")
    elif days_left <= 0:
        logger.warning("Local server certificate has expired (%d days), re-requesting...", days_left)
    else:
        logger.info("Local server certificate expiring soon (%d days remaining), renewing...", days_left)

    try:
        success = await request_server_certificate_from_server(manager, days_before_renew)

        if success:
            logger.info("Server certificate request successful")
            return True

        logger.error("Server certificate request failed, will start in non-TLS mode")
        return False

    except Exception as e:
        logger.error("Server certificate request exception: %s, will start in non-TLS mode", str(e))
        return False


async def certificate_renewal_task(
    days_before_renew: int = 30,
    check_interval_hours: int = 24,
) -> None:
    """
    Background periodic certificate renewal task

    Flow:
    1. Check server certificate every check_interval_hours hours
    2. If certificate has expired or is expiring soon (remaining ≤ days_before_renew days), request new certificate from server
    3. After successful request, set global flag for gRPC server to detect and restart

    Args:
        days_before_renew: How many days in advance to start renewal
        check_interval_hours: Check interval (hours)
    """
    global _certificate_renewed_needs_restart

    check_interval_seconds = check_interval_hours * 3600

    logger.info(
        "Background certificate renewal task started: check every %d hours, renew %d days in advance",
        check_interval_hours, days_before_renew
    )

    while True:
        try:
            await asyncio.sleep(check_interval_seconds)

            logger.info("=== Periodic certificate check ===")
            tls_dir = settings.get_tls_dir()
            manager = TLSCertificateManager(tls_dir)

            success = await request_server_certificate_from_server(
                manager, days_before_renew=days_before_renew
            )

            if success:
                days_left = manager.check_server_cert_expiry(days_before_renew)
                if days_left is not None and days_left > 0:
                    # Certificate actually updated to a new valid certificate
                    logger.info(
                        "Certificate renewal successful, new certificate valid for %d more days. "
                        "Will take effect on next gRPC server restart.",
                        days_left
                    )
                    _certificate_renewed_needs_restart = True
                else:
                    logger.warning("Certificate request successful but unable to verify new certificate validity")

        except asyncio.CancelledError:
            logger.info("Certificate renewal task cancelled")
            break
        except Exception as e:
            logger.error("Certificate renewal task exception: %s", str(e), exc_info=True)
            # Continue to next check after exception

    logger.info("Certificate renewal task has exited")


def main() -> None:
    """
    Main entry: check certificate first -> start gRPC server + background renewal task

    Note: After certificate renewal succeeds, gRPC server needs to reload the certificate.
    To avoid complex runtime dynamic replacement, use SIGUSR1 signal to trigger server restart.
    The server will re-read certificate files from disk on restart.
    """
    import os as _os

    # Step 1: Check and request certificate on startup
    logger.info("=== Taurus Executor Starting ===")
    has_valid_cert = asyncio.run(ensure_tls_certificates())

    if not has_valid_cert:
        logger.warning("Unable to obtain valid server certificate, will start in non-TLS mode")
    else:
        logger.info("Server certificate check passed")

    # Step 2: Start gRPC server + background renewal task
    try:
        from executor_core.services.server import serve_with_renewal_tasks
        # Serve with renewal tasks (will monitor certificate update flag and auto-restart server)
        asyncio.run(serve_with_renewal_tasks(days_before_renew=30, check_interval_hours=24))
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