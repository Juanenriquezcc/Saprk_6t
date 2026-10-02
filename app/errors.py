"""Application errors: always a readable message and, when possible, a hint."""


class AppError(Exception):
    def __init__(self, message, hint=None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def short_error(exc, limit=300):
    """First useful line of an exception (Spark errors include JVM stack traces)."""
    for line in str(exc).splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return type(exc).__name__
