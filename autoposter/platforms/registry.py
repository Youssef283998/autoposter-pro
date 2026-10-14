"""Platform registry: decorator-based discovery.

Adding a platform = one file in ``platforms/`` with a class decorated by
``@register_platform``. The dispatcher resolves adapters through this registry.
"""

from __future__ import annotations

from autoposter.config.schema import PlatformCaps
from autoposter.core.errors import PlatformError
from autoposter.core.models import AccountCredentials
from autoposter.platforms.base import Platform

_PLATFORM_CLASSES: dict[str, type[Platform]] = {}


def register_platform(cls: type[Platform]) -> type[Platform]:
    """Class decorator that registers a platform adapter."""
    name = getattr(cls, "name", None)
    if not name:
        raise PlatformError(f"Platform class {cls.__name__} has no name attribute.")
    _PLATFORM_CLASSES[name] = cls
    return cls


def get_platform(name: str) -> type[Platform]:
    """Return the adapter class for a platform name."""
    try:
        return _PLATFORM_CLASSES[name]
    except KeyError:
        known = ", ".join(sorted(_PLATFORM_CLASSES)) or "(none)"
        raise PlatformError(
            f"Unknown platform {name!r}.",
            fix=f"Available platforms: {known}. Run `autoposter setup {name}` to configure it.",
        ) from None


def list_platforms() -> list[str]:
    """All registered platform names."""
    return sorted(_PLATFORM_CLASSES)


def is_registered(name: str) -> bool:
    """Whether a platform name is registered."""
    return name in _PLATFORM_CLASSES


def build_adapter(
    name: str, account: AccountCredentials, settings: PlatformCaps
) -> Platform:
    """Instantiate an adapter for (platform, account, settings)."""
    cls = get_platform(name)
    return cls(account, settings)
