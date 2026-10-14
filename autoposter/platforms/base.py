"""Platform adapter base class and shared contracts.

Re-exports the core value objects so adapters only import from ``platforms``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from autoposter.config.schema import PlatformCaps
from autoposter.core.models import (
    AccountCredentials,
    ErrorCategory,
    HealthStatus,
    PlatformLimits,
    Post,
    PostTarget,
    PublishResult,
    ValidationIssue,
)

__all__ = [
    "AccountCredentials",
    "ErrorCategory",
    "HealthStatus",
    "Platform",
    "PlatformLimits",
    "Post",
    "PostTarget",
    "PublishResult",
    "ValidationIssue",
]


class Platform(ABC):
    """Every platform implements this contract.

    The dispatcher contains zero platform-specific code; it only calls these
    methods. Adding a platform means subclassing this and registering.
    """

    name: ClassVar[str]
    limits: ClassVar[PlatformLimits]

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        self._account = account
        self._settings = settings

    @abstractmethod
    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        """Pre-flight validation: char limits, media count, MIME, size."""
        ...

    @abstractmethod
    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        """Publish one post to one target. Never raises for API errors."""
        ...

    @abstractmethod
    async def refresh_auth(self) -> None:
        """Proactively or reactively refresh OAuth tokens."""
        ...

    @abstractmethod
    async def healthcheck(self) -> HealthStatus:
        """Verify credentials and API reachability."""
        ...

    async def find_recent_by_hash(self, content_hash: str) -> str | None:
        """Optional: reconcile AMBIGUOUS outcomes by searching recent posts."""
        return None

    async def aclose(self) -> None:
        """Release resources (HTTP clients, etc.)."""
        return None
