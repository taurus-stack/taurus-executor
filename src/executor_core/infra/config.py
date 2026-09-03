import os
import sys
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Version
    current_version: str = Field(default="1.0.1", env="EXECUTOR_VERSION")

    # gRPC Server
    grpc_host: str = Field(default="0.0.0.0", env="GRPC_HOST")
    grpc_port: int = Field(default=50051, env="GRPC_PORT")
    
    # Metrics (disabled by default, Supervisor manages process-level monitoring)
    metrics_enabled: bool = Field(default=False, env="METRICS_ENABLED")
    metrics_port: int = Field(default=9090, env="METRICS_PORT")
    
    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(default="text", env="LOG_FORMAT")  # 'json' or 'text'
    log_dir: str = Field(default="", env="LOG_DIR")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, env="LOG_MAX_BYTES")  # Default 10MB
    log_backup_count: int = Field(default=5, env="LOG_BACKUP_COUNT")  # Default keep 5 backups

    def get_log_dir(self) -> Path:
        """Get log directory"""
        if self.log_dir:
            return Path(self.log_dir)
        
        # After packaging: use ~/.taurus-executor/logs or /var/log/taurus-executor
        if getattr(sys, 'frozen', False):
            for p in [
                Path('/var/log/taurus-executor'),
                Path.home() / '.taurus-executor' / 'logs',
            ]:
                if p.exists():
                    return p
            return Path.home() / '.taurus-executor' / 'logs'
        
        # Development environment: use project root logs/
        project_root = Path(__file__).resolve().parents[3]
        return project_root / 'logs'

    # Security
    allow_commands: list[str] = Field(default_factory=lambda: ["ls", "ps", "cat", "grep"])
    allow_file_outside_home: bool = Field(default=True, env="ALLOW_FILE_OUTSIDE_HOME")
    
    # TLS certificate paths
    # Prefer environment variables, otherwise auto-detect based on runtime mode
    tls_cert_path: Optional[str] = Field(default=None, env="TLS_CERT_PATH")
    tls_key_path: Optional[str] = Field(default=None, env="TLS_KEY_PATH")
    tls_ca_path: Optional[str] = Field(default=None, env="TLS_CA_PATH")
    tls_dir: Optional[str] = Field(default=None, env="TLS_DIR")
    
    # Server-specific TLS certificate paths (for gRPC server)
    tls_server_cert_path: Optional[str] = Field(default=None, env="TLS_SERVER_CERT_PATH")
    tls_server_key_path: Optional[str] = Field(default=None, env="TLS_SERVER_KEY_PATH")

    def get_tls_dir(self) -> Path:
        """Get TLS certificate directory"""
        if self.tls_dir:
            return Path(self.tls_dir)
        
        # After packaging: use ~/.taurus-executor/tls or /etc/taurus-executor/tls
        if getattr(sys, 'frozen', False):
            for p in [
                Path.home() / '.taurus-executor' / 'tls',
                Path('/etc/taurus-executor/tls'),
            ]:
                if p.exists():
                    return p
            return Path.home() / '.taurus-executor' / 'tls'
        
        # Development environment: use project root tls/
        project_root = Path(__file__).resolve().parents[3]
        return project_root / 'tls'

    def get_tls_files(self) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Return client certificate (client_cert, client_key, ca) paths, auto-detect if not set"""
        tls_dir = self.get_tls_dir()
        
        cert = Path(self.tls_cert_path) if self.tls_cert_path else tls_dir / 'client.crt'
        key = Path(self.tls_key_path) if self.tls_key_path else tls_dir / 'client.key'
        ca = Path(self.tls_ca_path) if self.tls_ca_path else tls_dir / 'ca.crt'
        
        return cert, key, ca

    def get_server_tls_files(self) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Return server certificate (server_cert, server_key, ca) paths, auto-detect if not set"""
        tls_dir = self.get_tls_dir()
        
        # Prefer server-specific certificate paths
        cert = Path(self.tls_server_cert_path) if self.tls_server_cert_path else tls_dir / 'server.crt'
        key = Path(self.tls_server_key_path) if self.tls_server_key_path else tls_dir / 'server.key'
        ca = Path(self.tls_ca_path) if self.tls_ca_path else tls_dir / 'ca.crt'
        
        return cert, key, ca

    # Ticket authentication configuration
    ticket_auth_enabled: bool = Field(default=False, env="TICKET_AUTH_ENABLED")  # Disabled by default
    auth_service_url: str = Field(default="http://localhost:8001", env="AUTH_SERVICE_URL")
    auth_verify_timeout: int = Field(default=3, env="AUTH_VERIFY_TIMEOUT")
    auth_verify_retry_count: int = Field(default=2, env="AUTH_VERIFY_RETRY_COUNT")
    auth_fallback_policy: str = Field(default="deny", env="AUTH_FALLBACK_POLICY")  # deny or allow

    model_config = SettingsConfigDict(extra="ignore")

# Global settings instance
settings = Settings()