"""
Certificate request client
Responsible for requesting and renewing TLS certificates from the server
"""
import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import httpx

from .tls_manager import TLSCertificateManager

logger = logging.getLogger(__name__)


def load_supervisor_config() -> Tuple[Optional[str], Optional[str]]:
    """
    Load configuration from Supervisor state file (production environment)
    
    Returns:
        (server_url, host_uuid) tuple
    """
    state_paths = [
        Path.home() / '.taurus' / 'data' / 'state.json',
        Path('/opt/taurus/data/state.json'),
        Path.home() / 'taurus' / 'data' / 'state.json',
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
                    return server_url, str(host_id)
            except Exception as e:
                logger.warning(f"Failed to read Supervisor state file {state_file}: {e}")
    
    return None, None


def get_server_config() -> Tuple[Optional[str], Optional[str]]:
    """
    Get server configuration
    
    Priority:
    1. Production: read from Supervisor state file
    2. Development: read from environment variables
    
    Returns:
        (server_url, host_uuid) tuple
    """
    # Production: read from Supervisor state file
    server_url, host_uuid = load_supervisor_config()
    
    if server_url and host_uuid:
        return server_url, host_uuid
    
    # Development: read from environment variables
    server_url = os.getenv("TAURUS_SERVER_URL")
    host_uuid = os.getenv("TAURUS_HOST_UUID")
    
    if server_url and host_uuid:
        logger.info("Loaded configuration from environment variables")
        return server_url, host_uuid
    
    return None, None


class CertificateClient:
    """Certificate request client"""

    def __init__(
        self,
        tls_manager: TLSCertificateManager,
        server_url: str,
        host_uuid: str,
    ):
        """
        Initialize the certificate client
        
        Args:
            tls_manager: TLS certificate manager instance
            server_url: Server URL (e.g. http://localhost:8000)
            host_uuid: Host UUID
        """
        self.tls_manager = tls_manager
        self.server_url = server_url.rstrip("/")
        self.host_uuid = host_uuid
        # Note: backend route is /api/taurus/host/ (singular), not /hosts/
        self.issue_url = f"{self.server_url}/api/taurus/host/{self.host_uuid}/issue_certificate/"

    async def request_certificate(self) -> bool:
        """
        Request certificate from server
        
        Flow:
        1. Generate CSR
        2. Send to server
        3. Receive and save certificate
        
        Returns:
            Whether the request was successful
        """
        try:
            # 1. Generate CSR
            logger.info("Generating CSR...")
            csr = self.tls_manager.generate_csr()
            logger.info("CSR generated successfully, length: %d characters", len(csr))
            
            # 2. Send to server
            logger.info("Requesting certificate from server: %s", self.issue_url)
            logger.info("Request body: %s", json.dumps({"csr": csr[:50] + "..."})[:100])
            
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                response = await client.post(
                    self.issue_url,
                    json={"csr": csr},
                )
                
                logger.info("Server response: HTTP %d", response.status_code)
                logger.info("Response content: %s", response.text[:500])
                
                if response.status_code != 200:
                    logger.error(
                        "Certificate request failed: HTTP %d, %s",
                        response.status_code,
                        response.text,
                    )
                    return False
                
                data = response.json()
                logger.info("Parsed response: code=%s, msg=%s", data.get("code"), data.get("msg"))
                
                # 3. Check response
                if data.get("code") != 2000:
                    logger.error("Certificate request failed: %s", data.get("msg", "Unknown error"))
                    return False
                
                cert_data = data.get("data", {})
                ca_cert = cert_data.get("ca_cert")
                client_cert = cert_data.get("client_cert")
                serial = cert_data.get("serial")
                
                logger.info("Certificate data: serial=%s, has_ca=%s, has_client=%s", 
                           serial, bool(ca_cert), bool(client_cert))
                
                if not ca_cert or not client_cert:
                    logger.error("Server response missing certificate content")
                    return False
                
                # 4. Save certificate
                logger.info("Saving certificate to: %s", self.tls_manager.tls_dir)
                if self.tls_manager.save_certificates(ca_cert, client_cert):
                    logger.info(
                        "Certificate request successful: serial=%s, expires_in=%d days",
                        serial,
                        cert_data.get("expires_in_days", 0),
                    )
                    return True
                else:
                    logger.error("Failed to save certificate")
                    return False
                    
        except httpx.ConnectError as e:
            logger.error("Unable to connect to server: %s", str(e))
            return False
        except Exception as e:
            logger.error("Certificate request exception: %s", str(e), exc_info=True)
            return False

    async def ensure_certificate(self) -> bool:
        """
        Ensure certificate is valid
        
        Flow:
        1. Check local certificate
        2. If not exists, request new certificate
        3. If expiring soon, renew certificate
        
        Returns:
            Whether certificate is valid
        """
        logger.info("Ensuring certificate is valid...")
        
        # Check certificate status
        cert, key, ca = (
            self.tls_manager.client_cert,
            self.tls_manager.client_key,
            self.tls_manager.ca_cert,
        )
        
        logger.info("Checking certificate files: cert=%s, key=%s, ca=%s", 
                   cert.exists(), key.exists(), ca.exists())
        
        # If all certificates exist, check if renewal is needed
        if cert.exists() and key.exists() and ca.exists():
            # Check expiration time
            import subprocess
            from datetime import datetime, timezone
            
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-enddate", "-noout", "-in", str(cert)],
                    capture_output=True, text=True, check=True
                )
                end_date_str = result.stdout.strip().split("=", 1)[1]
                end_date = datetime.strptime(
                    end_date_str, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                
                days_left = (end_date - datetime.now(timezone.utc)).days
                
                if days_left > 30:
                    logger.info("Certificate is valid, %d days remaining", days_left)
                    return True
                
                logger.info("Certificate will expire in %d days, renewing...", days_left)
                
            except Exception as e:
                logger.warning("Unable to check certificate expiration: %s", str(e))
        
        # Request new certificate or renewal
        logger.info("Requesting certificate...")
        return await self.request_certificate()

    async def request_server_certificate(self) -> bool:
        """
        Request server certificate from server
        
        Flow:
        1. Generate server CSR
        2. Send to server
        3. Receive and save certificate (SAN auto-generated by Backend)
        
        Returns:
            Whether the request was successful
        """
        try:
            # 1. Generate server CSR
            logger.info("Generating server CSR...")
            csr = self.tls_manager.generate_server_csr()
            logger.info("Server CSR generated successfully, length: %d characters", len(csr))
            
            # 2. Send to server
            issue_url = f"{self.server_url}/api/taurus/host/{self.host_uuid}/issue_server_certificate/"
            logger.info("Requesting server certificate from server: %s", issue_url)
            
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                response = await client.post(
                    issue_url,
                    json={"csr": csr},
                )
                
                logger.info("Server response: HTTP %d", response.status_code)
                logger.info("Response content: %s", response.text[:500])
                
                if response.status_code != 200:
                    logger.error(
                        "Server certificate request failed: HTTP %d, %s",
                        response.status_code,
                        response.text,
                    )
                    return False
                
                data = response.json()
                logger.info("Parsed response: code=%s, msg=%s", data.get("code"), data.get("msg"))
                
                # 3. Check response
                if data.get("code") != 2000:
                    logger.error("Server certificate request failed: %s", data.get("msg", "Unknown error"))
                    return False
                
                cert_data = data.get("data", {})
                ca_cert = cert_data.get("ca_cert")
                server_cert = cert_data.get("server_cert")
                serial = cert_data.get("serial")
                
                logger.info("Server certificate data: serial=%s, has_ca=%s, has_server=%s", 
                           serial, bool(ca_cert), bool(server_cert))
                
                if not ca_cert or not server_cert:
                    logger.error("Server response missing certificate content")
                    return False
                
                # 4. Save server certificate
                logger.info("Saving server certificate to: %s", self.tls_manager.tls_dir)
                if self.tls_manager.save_server_certificates(ca_cert, server_cert):
                    logger.info(
                        "Server certificate request successful: serial=%s, expires_in=%d days",
                        serial,
                        cert_data.get("expires_in_days", 0),
                    )
                    return True
                else:
                    logger.error("Failed to save server certificate")
                    return False
                    
        except httpx.ConnectError as e:
            logger.error("Unable to connect to server: %s", str(e))
            return False
        except Exception as e:
            logger.error("Server certificate request exception: %s", str(e), exc_info=True)
            return False

    async def ensure_server_certificate(self, days_before_renew: int = 30) -> bool:
        """
        Ensure server certificate is valid (check expiration and auto-renew)

        Flow:
        1. Check if local server certificate exists and is not expired
        2. If not exists or expiring soon (remaining ≤ days_before_renew days), request new certificate from server

        Args:
            days_before_renew: How many days in advance to start renewal

        Returns:
            Whether a valid server certificate exists
        """
        logger.info("Checking server certificate validity (renew %d days in advance)...", days_before_renew)

        days_left = self.tls_manager.check_server_cert_expiry(days_before_renew)

        # Certificate does not exist or check failed
        if days_left is None:
            logger.info("Server certificate does not exist, requesting new certificate...")
            return await self.request_server_certificate()

        # Certificate has expired
        if days_left <= 0:
            logger.warning("Server certificate has expired (%d days), requesting new certificate...", days_left)
            return await self.request_server_certificate()

        # Certificate expiring soon
        if days_left <= days_before_renew:
            logger.info("Server certificate expiring soon (%d days remaining), requesting renewal...", days_left)
            return await self.request_server_certificate()

        # Certificate is valid
        logger.info("Server certificate is valid, %d days remaining, no renewal needed", days_left)
        return True


def get_certificate_client(
    tls_dir: Optional[Path] = None,
    server_url: Optional[str] = None,
    host_uuid: Optional[str] = None,
) -> Optional[CertificateClient]:
    """
    Get a certificate client instance
    
    Args:
        tls_dir: TLS certificate directory
        server_url: Server URL
        host_uuid: Host UUID
        
    Returns:
        Certificate client instance, or None if required parameters are missing
    """
    logger.info("Initializing certificate client...")
    logger.info("Parameters: tls_dir=%s, server_url=%s, host_uuid=%s", 
               tls_dir, server_url, host_uuid)
    
    # Prefer get_server_config() to get configuration (supports Supervisor state file and environment variables)
    if not server_url or not host_uuid:
        logger.info("Attempting to get configuration from Supervisor state file or environment variables...")
        config_server_url, config_host_uuid = get_server_config()
        server_url = server_url or config_server_url
        host_uuid = host_uuid or config_host_uuid
        logger.info("Retrieved configuration: server_url=%s, host_uuid=%s", server_url, host_uuid)
    
    if not server_url or not host_uuid:
        logger.warning(
            "Missing server configuration: please ensure Supervisor is registered or set environment variables TAURUS_SERVER_URL and TAURUS_HOST_UUID"
        )
        return None
    
    tls_dir = tls_dir or Path.home() / ".taurus-executor" / "tls"
    logger.info("Using TLS directory: %s", tls_dir)
    
    tls_manager = TLSCertificateManager(tls_dir)
    
    client = CertificateClient(
        tls_manager=tls_manager,
        server_url=server_url,
        host_uuid=host_uuid,
    )
    
    logger.info("Certificate client initialized successfully: issue_url=%s", client.issue_url)
    return client