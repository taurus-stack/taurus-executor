"""
TLS certificate manager
Responsible for managing and verifying client certificates
"""
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TLSCertificateManager:
    """TLS certificate manager"""

    def __init__(self, tls_dir: Path):
        self.tls_dir = tls_dir
        # Client certificates
        self.ca_cert = tls_dir / "ca.crt"
        self.client_key = tls_dir / "client.key"
        self.client_cert = tls_dir / "client.crt"
        # Server certificates
        self.server_key = tls_dir / "server.key"
        self.server_cert = tls_dir / "server.crt"

    def ensure_certificates(self, days_before_renew: int = 30) -> bool:
        """
        Ensure client certificates exist and are not expired
        Note: Certificates are signed by server, client only handles verification and renewal requests
        
        Args:
            days_before_renew: How many days in advance to request renewal
            
        Returns:
            Whether certificates are valid
        """
        needs_renewal = self._check_renewal_needed(days_before_renew)
        
        if not needs_renewal:
            logger.info("TLS certificates are valid, no renewal needed")
            return True
        
        if needs_renewal == "generate":
            logger.info("TLS certificates not found")
        else:
            logger.info("TLS certificates expiring soon, renewal required from server")
        
        logger.warning("Certificates need renewal, please re-obtain from server")
        return False

    def _check_renewal_needed(self, days_before_renew: int) -> Optional[str]:
        """
        Check if renewal is needed
        
        Returns:
            None: No renewal needed
            "generate": Need to generate (certificates don't exist)
            "renew": Need to renew (certificates expiring soon)
        """
        required_files = [self.ca_cert, self.client_key, self.client_cert]
        missing = [f for f in required_files if not f.exists()]
        
        if missing:
            logger.debug(f"Missing TLS files: {[str(f) for f in missing]}")
            return "generate"
        
        try:
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", str(self.client_cert)],
                capture_output=True,
                text=True,
                check=True
            )
            end_date_str = result.stdout.strip().split("=", 1)[1]
            end_date = datetime.strptime(end_date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            days_left = (end_date - now).days
            
            if days_left <= 0:
                logger.warning("TLS certificate has expired!")
                return "renew"
            elif days_left <= days_before_renew:
                logger.info(f"TLS certificate expires in {days_left} days")
                return "renew"
            else:
                logger.debug(f"TLS certificate valid for {days_left} more days")
                return None
                
        except Exception as e:
            logger.error(f"Failed to check certificate expiry: {e}")
            return "renew"

    def generate_csr(self) -> str:
        """
        Generate certificate signing request (CSR)
        Used to apply for certificate from server
        
        Returns:
            CSR content (PEM format)
        """
        logger.info("Generating certificate signing request (CSR)...")
        
        self.tls_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.client_key.exists():
            subprocess.run(
                ["openssl", "genrsa", "-out", str(self.client_key), "2048"],
                check=True,
                capture_output=True
            )
            os.chmod(self.client_key, 0o600)
            logger.info("Client private key generated")
        
        csr_path = self.tls_dir / "client.csr"
        hostname = os.uname().nodename
        
        subprocess.run(
            ["openssl", "req", "-new", "-key", str(self.client_key),
             "-out", str(csr_path),
             "-subj", f"/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN={hostname}"],
            check=True,
            capture_output=True
        )
        
        csr_content = csr_path.read_text()
        csr_path.unlink(missing_ok=True)
        
        logger.info("CSR generated successfully")
        return csr_content

    def save_certificates(self, ca_cert: str, client_cert: str) -> bool:
        """
        Save certificates obtained from server
        
        Args:
            ca_cert: CA certificate content (PEM format)
            client_cert: Client certificate content (PEM format)
            
        Returns:
            Whether saved successfully
        """
        try:
            self.tls_dir.mkdir(parents=True, exist_ok=True)
            
            self.ca_cert.write_text(ca_cert)
            os.chmod(self.ca_cert, 0o644)
            
            self.client_cert.write_text(client_cert)
            os.chmod(self.client_cert, 0o644)
            
            logger.info("Certificates saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save certificates: {e}")
            return False

    def generate_server_csr(self) -> str:
        """
        Generate server certificate signing request (CSR)
        Used to apply for server certificate from server
        
        Returns:
            CSR content (PEM format)
        """
        logger.info("Generating server certificate signing request (CSR)...")
        
        self.tls_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.server_key.exists():
            subprocess.run(
                ["openssl", "genrsa", "-out", str(self.server_key), "2048"],
                check=True,
                capture_output=True
            )
            os.chmod(self.server_key, 0o600)
            logger.info("Server private key generated")
        
        csr_path = self.tls_dir / "server.csr"
        hostname = os.uname().nodename
        
        subprocess.run(
            [
                "openssl", "req", "-new", "-key", str(self.server_key),
                "-out", str(csr_path),
                "-subj", f"/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN={hostname}"
            ],
            check=True, capture_output=True
        )
        
        csr_content = csr_path.read_text()
        csr_path.unlink(missing_ok=True)
        
        logger.info("Server CSR generated successfully")
        return csr_content

    def check_server_cert_expiry(self, days_before_renew: int = 30) -> Optional[int]:
        """
        Check if server certificate is about to expire

        Args:
            days_before_renew: How many days in advance to consider renewal needed

        Returns:
            int: Remaining certificate validity days
                > days_before_renew: No renewal needed
                0 ~ days_before_renew: Expiring soon, renewal needed
                <= 0: Expired
            None: Certificate doesn't exist or check failed (needs application)
        """
        if not self.server_cert.exists() or not self.server_key.exists():
            logger.debug("Server certificate files not found")
            return None

        try:
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", str(self.server_cert)],
                capture_output=True, text=True, check=True
            )
            end_date_str = result.stdout.strip().split("=", 1)[1]
            end_date = datetime.strptime(
                end_date_str, "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            days_left = (end_date - now).days

            if days_left <= 0:
                logger.warning("Server certificate has EXPIRED! (%d days)", days_left)
            elif days_left <= days_before_renew:
                logger.info("Server certificate expires in %d days, renewal recommended", days_left)
            else:
                logger.debug("Server certificate valid for %d more days", days_left)

            return days_left

        except Exception as e:
            logger.error(f"Failed to check server certificate expiry: {e}")
            return None

    def save_server_certificates(self, ca_cert: str, server_cert: str) -> bool:
        """
        Save server certificates obtained from server

        Args:
            ca_cert: CA certificate content (PEM format)
            server_cert: Server certificate content (PEM format)

        Returns:
            Whether saved successfully
        """
        try:
            self.tls_dir.mkdir(parents=True, exist_ok=True)

            # Save CA certificate (only if not exists)
            if not self.ca_cert.exists():
                self.ca_cert.write_text(ca_cert)
                os.chmod(self.ca_cert, 0o644)

            self.server_cert.write_text(server_cert)
            os.chmod(self.server_cert, 0o644)

            logger.info("Server certificates saved successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to save server certificates: {e}")
            return False