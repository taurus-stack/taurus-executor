"""
Custom exceptions for the Taurus Executor core.

This module defines a hierarchy of custom exceptions to provide more specific
and context-rich error handling throughout the application.
"""

from typing import Optional, Any, Dict

class ClientCoreError(Exception):
    """
    Base exception for all executor_core related errors.
    All custom exceptions should inherit from this class.
    """
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self):
        if self.details:
            # Provide a more informative string representation
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} (Details: {details_str})"
        return self.message

# --- Command Execution Related Errors ---

class CommandExecutionError(ClientCoreError):
    """Base class for errors during command execution."""
    def __init__(self, message: str, command: str, exit_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.command = command
        self.exit_code = exit_code
        # Update message with command context
        self.message = f"Command '{command}' failed. {message}"
        if exit_code is not None:
            self.message += f" (Exit Code: {exit_code})"

class CommandTimeoutError(CommandExecutionError):
    """Raised when a command execution exceeds its timeout limit."""
    def __init__(self, command: str, timeout_seconds: int):
        super().__init__(
            message=f"Command timed out after {timeout_seconds} seconds.",
            command=command,
            details={"timeout_seconds": timeout_seconds}
        )
        self.timeout_seconds = timeout_seconds

class CommandNotFoundError(CommandExecutionError):
    """Raised when the requested command executable is not found."""
    def __init__(self, command: str):
        super().__init__(
            message="Executable not found.",
            command=command,
            details={"error_type": "FILE_NOT_FOUND"}
        )

class PermissionDeniedError(CommandExecutionError):
    """Raised when a command cannot be executed due to insufficient permissions."""
    def __init__(self, command: str, user: Optional[str] = None):
        super().__init__(
            message="Permission denied.",
            command=command,
            details={"user": user} if user else {}
        )

# --- Updater Related Errors ---

class UpdateError(ClientCoreError):
    """Base class for all errors during the self-update process."""
    def __init__(self, message: str, current_version: str, target_version: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.current_version = current_version
        self.target_version = target_version
        self.message = f"Update failed from '{current_version}'. {message}"
        if target_version:
            self.message += f" (Target: '{target_version}')"

class DownloadError(UpdateError):
    """Raised when downloading the new version fails."""
    def __init__(self, url: str, status_code: Optional[int] = None, reason: Optional[str] = None):
        super().__init__(
            message="Failed to download update package.",
            current_version="N/A", # This error might occur before version is known
            details={"url": url, "status_code": status_code, "reason": reason}
        )
        self.url = url

class VerificationError(UpdateError):
    """Raised when the downloaded package fails verification (e.g., checksum mismatch)."""
    def __init__(self, current_version: str, target_version: str, expected_checksum: str, actual_checksum: str):
        super().__init__(
            message="Checksum verification failed. The package may be corrupted or tampered with.",
            current_version=current_version,
            target_version=target_version,
            details={
                "expected_checksum": expected_checksum,
                "actual_checksum": actual_checksum
            }
        )

class InstallationError(UpdateError):
    """Raised when the installation step (e.g., moving files) fails."""
    def __init__(self, current_version: str, target_version: str, reason: str):
        super().__init__(
            message=f"Could not install the new version: {reason}",
            current_version=current_version,
            target_version=target_version
        )

# --- Deployment Related Errors ---

class DeploymentError(ClientCoreError):
    """Base class for errors during remote deployment via SSH."""
    def __init__(self, message: str, host: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.host = host
        self.message = f"Deployment to host '{host}' failed. {message}"

class SSHConnectionError(DeploymentError):
    """Raised when the SSH connection cannot be established."""
    def __init__(self, host: str, user: str, reason: str):
        super().__init__(
            message=f"SSH connection failed for user '{user}': {reason}",
            host=host,
            details={"user": user}
        )

class RemoteCommandError(DeploymentError):
    """Raised when a command executed on the remote host fails."""
    def __init__(self, host: str, command: str, exit_code: int, stderr: Optional[str] = None):
        super().__init__(
            message=f"Remote command '{command}' failed.",
            host=host,
            details={
                "command": command,
                "exit_code": exit_code,
                "stderr": stderr
            }
        )
        self.command = command
        self.exit_code = exit_code

class EnvironmentCheckError(DeploymentError):
    """Raised when the target environment does not meet requirements."""
    def __init__(self, host: str, missing_requirements: list[str]):
        super().__init__(
            message="Target environment check failed.",
            host=host,
            details={"missing_requirements": missing_requirements}
        )

# --- gRPC / Service Related Errors ---

class ServiceError(ClientCoreError):
    """Base class for gRPC service layer errors."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)

class AuthenticationError(ServiceError):
    """Raised when client authentication fails (e.g., bad certificate)."""
    def __init__(self, reason: str = "Invalid credentials"):
        super().__init__(message=f"Authentication failed: {reason}")

class AuthorizationError(ServiceError):
    """Raised when a client is authenticated but not authorized for an action."""
    def __init__(self, action: str, role: Optional[str] = None):
        super().__init__(
            message=f"Authorization denied for action '{action}'.",
            details={"action": action, "role": role}
        )
        self.action = action

class InvalidRequestError(ServiceError):
    """Raised when the client sends a malformed or invalid request."""
    def __init__(self, reason: str, field: Optional[str] = None):
        super().__init__(
            message=f"Invalid request: {reason}",
            details={"field": field} if field else {}
        )