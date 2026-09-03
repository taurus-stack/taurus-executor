class TaurusClientError(Exception):
    """Base exception for all Taurus client errors."""
    pass


class ConnectionError(TaurusClientError):
    """Raised when there's an error connecting to the server."""
    pass


class CommandExecutionError(TaurusClientError):
    """Raised when a command execution fails on the server."""
    pass


class CommandTimeoutError(TaurusClientError):
    """Raised when a command execution times out."""
    pass


class AuthenticationError(TaurusClientError):
    """Raised when there's an authentication issue with the server."""
    pass


class ServerError(TaurusClientError):
    """Raised when the server returns an error response."""
    pass


class InvalidRequestError(TaurusClientError):
    """Raised when the client sends an invalid request."""
    pass