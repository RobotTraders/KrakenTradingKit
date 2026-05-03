class APIError(Exception):
    """Raised when the Kraken API returns an error response."""

    def __init__(self, errors: list[str] | str) -> None:
        if isinstance(errors, list):
            message = "; ".join(str(e) for e in errors)
        else:
            message = str(errors)
        super().__init__(message)
        self.errors = errors


class AuthError(Exception):
    """Raised when authentication is required but not configured."""
