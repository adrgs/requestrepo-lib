"""Exceptions for the requestrepo client library."""


class RequestRepoError(Exception):
    """Base exception for all requestrepo errors."""


class AuthError(RequestRepoError):
    """Token is missing, invalid, or expired."""


class SessionError(RequestRepoError):
    """Session creation failed (network, rate limit, or admin token required)."""


class RateLimitError(RequestRepoError):
    """Server rate limit exceeded.

    Attributes:
        retry_after: Seconds until the rate limit resets.
    """

    def __init__(self, message: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RequestRepoConnectionError(RequestRepoError):
    """WebSocket or HTTP connection failure.

    Named to avoid shadowing the built-in ConnectionError.
    """


class RequestRepoTimeoutError(RequestRepoError):
    """A wait_for_* or listen() call exceeded its timeout.

    Named to avoid shadowing the built-in TimeoutError.
    """


class NotFoundError(RequestRepoError):
    """Requested resource (request, file, etc.) does not exist."""


class APIError(RequestRepoError):
    """Unexpected HTTP error from the API.

    Attributes:
        status_code: HTTP status code from the server.
        code: Machine-readable error code.
    """

    def __init__(
        self, message: str, status_code: int = 0, code: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
