from typing import Optional


class ClientError(Exception):
    """Base exception for all client errors."""


class ConnectionError(ClientError):
    """Raised when a client cannot establish a connection."""

    def __init__(self, message: str, host: str = "", port: int = 0):
        self.host = host
        self.port = port
        super().__init__(message)


class TimeoutError(ClientError):
    """Raised when a client operation times out."""

    def __init__(self, message: str, timeout: Optional[float] = None):
        self.timeout = timeout
        super().__init__(message)


class AuthenticationError(ClientError):
    """Raised when authentication fails."""


class QueryError(ClientError):
    """Raised when a database query or operation fails."""

    def __init__(self, message: str, query: str = ""):
        self.query = query
        super().__init__(message)


class RetryExhaustedError(ClientError):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, attempt_count: int):
        self.attempt_count = attempt_count
        super().__init__(message)
