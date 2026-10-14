"""Typed error taxonomy for AutoPoster.

Every user-facing error carries an optional ``fix`` string: a plain-English,
copy-pasteable remediation (usually a command). CLI layers print
``message`` + ``fix`` and reserve tracebacks for ``--debug``.
"""

from __future__ import annotations


class AutoposterError(Exception):
    """Base class for all AutoPoster errors.

    Attributes:
        message: Human-readable description of what went wrong.
        fix: Plain-English remediation, ideally an exact command to run.
    """

    def __init__(self, message: str, *, fix: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix

    def __str__(self) -> str:
        if self.fix:
            return f"{self.message}\n  Fix: {self.fix}"
        return self.message


class ConfigError(AutoposterError):
    """Invalid or missing configuration (.env / config.yaml)."""


class DatabaseError(AutoposterError):
    """Database access, migration or integrity failure."""


class LockHeldError(DatabaseError):
    """Another AutoPoster instance already owns this database."""


class PostValidationError(AutoposterError):
    """A post failed pre-flight validation (length, media, targets...)."""


class MediaError(AutoposterError):
    """Media file missing, unreadable, oversized or wrong type."""


class AuthError(AutoposterError):
    """Credential storage, encryption or refresh failure."""


class PlatformError(AutoposterError):
    """A platform adapter could not be loaded or is misconfigured."""


class DuplicatePostError(AutoposterError):
    """Duplicate prevention blocked this post (dedup.mode == block)."""


class MigrationError(DatabaseError):
    """Schema migration failed or is out of date."""
